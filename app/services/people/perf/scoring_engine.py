"""
OHCSF 3-Step Composite Scoring Engine.

Implements the performance scoring formula from Nigeria's Office of the Head
of the Civil Service of the Federation (OHCSF) Performance Management System
(PMS) Guidelines.

Steps:
    1. Calculate raw achievement score per KPI by interpolating actual
       achievement within OHCSF threshold bands.
    2. Multiply raw score by the KPI weight → weighted raw score.
    3. Sum all weighted raw scores → composite score (0–100%).

Final employee score:
    (objective_composite × 0.70) + (competency_score × 0.20) + (process_score × 0.10)
"""

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.services.people.perf.performance_policy import get_policy_profile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rating Scale
# ---------------------------------------------------------------------------

#: OHCSF rating scale mapping rating integer → label and minimum percentage.
#: Rating 5 = Outstanding (≥90%), 1 = Poor (<60%).
OHCSF_RATING_SCALE: dict[int, dict[str, Any]] = {
    5: {"label": "Outstanding", "min_pct": Decimal("90")},
    4: {"label": "Excellent", "min_pct": Decimal("80")},
    3: {"label": "Good", "min_pct": Decimal("70")},
    2: {"label": "Fair", "min_pct": Decimal("60")},
    1: {"label": "Poor", "min_pct": Decimal("0")},
}

# ---------------------------------------------------------------------------
# Band percentage anchors (score % awarded at each threshold level)
# ---------------------------------------------------------------------------
# Map each threshold name → score percentage earned if actual == that threshold.
_BAND_PCT: dict[str, Decimal] = {
    "outstanding": Decimal("100"),
    "excellent": Decimal("90"),
    "good": Decimal("80"),
    "fair": Decimal("70"),
    "poor": Decimal("60"),
}

# Ordered bands for ascending KPIs (higher actual value is better)
_ASCENDING_BANDS: list[str] = ["outstanding", "excellent", "good", "fair", "poor"]

TWO_DP = Decimal("0.01")


class OHCSFScoringEngine:
    """Implements the OHCSF 3-step composite scoring formula."""

    def __init__(self, policy_profile_name: str = "GOVERNMENT_PMS") -> None:
        self._policy = get_policy_profile(policy_profile_name)
        self._rating_scale = {
            band.rating: {"label": band.label, "min_pct": band.min_pct}
            for band in self._policy.rating_scale
        }
        self._band_pct = dict(self._policy.kpi_band_percentages)

    # ------------------------------------------------------------------
    # Step 1 — Raw achievement score
    # ------------------------------------------------------------------

    def calculate_raw_score(
        self,
        actual: Decimal,
        thresholds: dict[str, Decimal],
    ) -> Decimal:
        """Calculate raw achievement score percentage for a single KPI.

        Supports both ascending (higher=better) and descending (lower=better)
        KPIs.  Direction is inferred by comparing the outstanding vs poor
        threshold values.

        Args:
            actual: Actual achieved value for the KPI.
            thresholds: Dict with keys ``outstanding``, ``excellent``, ``good``,
                ``fair``, ``poor`` mapping to the numeric threshold for each band.

        Returns:
            Score percentage as Decimal, quantized to 2 d.p., in [0, 100].
        """
        outstanding = thresholds["outstanding"]
        poor = thresholds["poor"]

        ascending = outstanding >= poor
        if not ascending:
            # Invert: convert descending thresholds so we can reuse the same
            # ascending logic.  We reflect the actual and the thresholds around
            # the poor threshold so that *lower* actual values produce *higher*
            # scores.
            score = self._raw_score_descending(actual, thresholds)
        else:
            score = self._raw_score_ascending(actual, thresholds)

        # Clamp to [0, 100]
        score = max(Decimal("0"), min(Decimal("100"), score))
        return score.quantize(TWO_DP, rounding=ROUND_HALF_UP)

    def _raw_score_ascending(
        self,
        actual: Decimal,
        thresholds: dict[str, Decimal],
    ) -> Decimal:
        """Ascending KPI: higher actual → higher score."""
        outstanding = thresholds["outstanding"]
        poor = thresholds["poor"]

        # At or above outstanding → 100%
        if actual >= outstanding:
            return Decimal("100")

        # Below poor threshold → proportional below 60%
        if actual <= poor:
            if poor == Decimal("0"):
                return Decimal("0")
            return (actual / poor) * Decimal("60")

        # Interpolate between adjacent bands
        for i in range(len(_ASCENDING_BANDS) - 1):
            upper_name = _ASCENDING_BANDS[i]  # e.g. "outstanding"
            lower_name = _ASCENDING_BANDS[i + 1]  # e.g. "excellent"

            upper_thr = thresholds[upper_name]
            lower_thr = thresholds[lower_name]
            upper_pct = self._band_pct[upper_name]
            lower_pct = self._band_pct[lower_name]

            if lower_thr <= actual < upper_thr:
                span_thr = upper_thr - lower_thr
                if span_thr == Decimal("0"):
                    return lower_pct
                ratio = (actual - lower_thr) / span_thr
                return lower_pct + ratio * (upper_pct - lower_pct)

        # Fallback (should not be reached)
        return Decimal("60")

    def _raw_score_descending(
        self,
        actual: Decimal,
        thresholds: dict[str, Decimal],
    ) -> Decimal:
        """Descending KPI: lower actual → higher score (e.g. error rate)."""
        outstanding = thresholds["outstanding"]  # smallest value (best)
        poor = thresholds["poor"]  # largest value (worst)

        # At or below outstanding threshold → 100%
        if actual <= outstanding:
            return Decimal("100")

        # Above poor threshold → proportional below 60%
        if actual >= poor:
            if actual == Decimal("0"):
                return Decimal("100")
            return (poor / actual) * Decimal("60")

        # Interpolate between adjacent bands (note: bands are in descending
        # threshold order, so "lower" threshold value = better performance)
        for i in range(len(_ASCENDING_BANDS) - 1):
            better_name = _ASCENDING_BANDS[i]  # lower threshold value = better
            worse_name = _ASCENDING_BANDS[i + 1]  # higher threshold value = worse

            better_thr = thresholds[better_name]
            worse_thr = thresholds[worse_name]
            better_pct = self._band_pct[better_name]
            worse_pct = self._band_pct[worse_name]

            if better_thr < actual <= worse_thr:
                span_thr = worse_thr - better_thr
                if span_thr == Decimal("0"):
                    return better_pct
                # As actual approaches worse_thr, score approaches worse_pct.
                # As actual approaches better_thr, score approaches better_pct.
                ratio = (worse_thr - actual) / span_thr
                return worse_pct + ratio * (better_pct - worse_pct)

        return Decimal("60")

    # ------------------------------------------------------------------
    # Step 2 — Weighted score
    # ------------------------------------------------------------------

    def calculate_weighted_score(
        self,
        raw_score_pct: Decimal,
        weight: Decimal,
    ) -> Decimal:
        """Multiply raw score percentage by KPI weight.

        Args:
            raw_score_pct: Raw achievement score (0–100).
            weight: KPI weight as a fraction (e.g. 0.50 for 50%).

        Returns:
            Weighted raw score as Decimal, quantized to 2 d.p.
        """
        result = raw_score_pct * weight
        return result.quantize(TWO_DP, rounding=ROUND_HALF_UP)

    # ------------------------------------------------------------------
    # Step 3 — Composite score
    # ------------------------------------------------------------------

    def calculate_composite(self, weighted_scores: list[Decimal]) -> Decimal:
        """Sum all weighted raw scores to produce the composite score.

        Args:
            weighted_scores: List of weighted raw scores from Step 2.

        Returns:
            Composite score as Decimal, quantized to 2 d.p.  Returns 0.00
            for an empty list.
        """
        if not weighted_scores:
            return Decimal("0.00")
        total = sum(weighted_scores, Decimal("0"))
        return total.quantize(TWO_DP, rounding=ROUND_HALF_UP)

    # ------------------------------------------------------------------
    # Final appraisal score
    # ------------------------------------------------------------------

    def calculate_appraisal_final(
        self,
        objective_composite: Decimal,
        competency_score: Decimal,
        process_score: Decimal,
    ) -> Decimal:
        """Calculate the final employee appraisal score.

        Applies the OHCSF 70/20/10 weighting across the three components:
        - Objectives performance  → 70%
        - Competency assessment   → 20%
        - Process/values score    → 10%

        Args:
            objective_composite: Composite score for objectives (0–100).
            competency_score: Competency assessment score (0–100).
            process_score: Process/values score (0–100).

        Returns:
            Final appraisal score as Decimal, quantized to 2 d.p.
        """
        weighted_objectives = (
            objective_composite * self._policy.appraisal_weights.objectives_pct
        )
        weighted_competency = (
            competency_score * self._policy.appraisal_weights.competencies_pct
        )
        weighted_process = process_score * self._policy.appraisal_weights.process_pct
        total = weighted_objectives + weighted_competency + weighted_process
        return total.quantize(TWO_DP, rounding=ROUND_HALF_UP)

    # ------------------------------------------------------------------
    # Rating label
    # ------------------------------------------------------------------

    def score_to_rating(self, composite_pct: Decimal) -> tuple[int, str]:
        """Map a composite score percentage to an OHCSF rating.

        Args:
            composite_pct: Score percentage (0–100).

        Returns:
            Tuple of (rating_int, label_string) where rating_int is 1–5.
        """
        for rating in sorted(self._rating_scale.keys(), reverse=True):
            entry = self._rating_scale[rating]
            if composite_pct >= entry["min_pct"]:
                return rating, entry["label"]
        # Fallback to Poor (should not be reached for valid input)
        return 1, self._rating_scale[1]["label"]
