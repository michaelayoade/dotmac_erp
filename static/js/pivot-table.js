function pivotAnalysis(initialCubes) {
  return {
    cubes: Array.isArray(initialCubes) ? initialCubes : [],
    cubeCode: "",
    activeDimensions: [],
    activeMeasures: [],
    activeCubeDescription: "",
    rowDimensions: [],
    measures: [],
    columns: [],
    rows: [],
    loading: false,
    error: "",

    init() {
      if (!this.cubes.length) {
        this.error = "No analysis cubes are configured.";
        return;
      }
      this.cubeCode = this.cubes[0].code;
      this.onCubeChange();
    },

    onCubeChange() {
      const cube = this.cubes.find((item) => item.code === this.cubeCode);
      if (!cube) {
        this.activeDimensions = [];
        this.activeMeasures = [];
        this.activeCubeDescription = "";
        this.rowDimensions = [];
        this.measures = [];
        return;
      }

      this.activeDimensions = Array.isArray(cube.dimensions) ? cube.dimensions : [];
      this.activeMeasures = Array.isArray(cube.measures) ? cube.measures : [];
      this.activeCubeDescription = cube.description || "";

      this.rowDimensions = Array.isArray(cube.default_rows) && cube.default_rows.length
        ? cube.default_rows.slice(0, 3)
        : (this.activeDimensions[0] ? [this.activeDimensions[0].field] : []);
      this.measures = Array.isArray(cube.default_measures) && cube.default_measures.length
        ? cube.default_measures.slice(0, 3)
        : (this.activeMeasures[0] ? [this.activeMeasures[0].field] : []);

      this.columns = [];
      this.rows = [];
      this.error = "";
    },

    toggleSelection(collection, value) {
      const idx = collection.indexOf(value);
      if (idx >= 0) {
        collection.splice(idx, 1);
      } else {
        collection.push(value);
      }
    },

    async runQuery() {
      this.error = "";
      if (!this.cubeCode) {
        this.error = "Select a cube first.";
        return;
      }
      if (!this.rowDimensions.length) {
        this.error = "Select at least one row dimension.";
        return;
      }
      if (!this.measures.length) {
        this.error = "Select at least one measure.";
        return;
      }

      this.loading = true;
      try {
        const response = await fetch(`/api/v1/analysis/${encodeURIComponent(this.cubeCode)}/query`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            row_dimensions: this.rowDimensions,
            measures: this.measures,
            limit: 1000
          })
        });
        const payload = await response.json();
        if (!response.ok) {
          this.error = payload.detail || "Analysis query failed.";
          this.columns = [];
          this.rows = [];
          return;
        }
        this.columns = Array.isArray(payload.columns) ? payload.columns : [];
        this.rows = Array.isArray(payload.rows) ? payload.rows : [];
      } catch (_err) {
        this.error = "Unable to reach analysis API.";
      } finally {
        this.loading = false;
      }
    }
  };
}
