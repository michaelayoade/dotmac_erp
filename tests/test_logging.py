import app.logging as logging_module


def test_configure_logging_suppresses_successful_celery_task_trace_records(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        logging_module.logging.config,
        "dictConfig",
        lambda config: captured.update(config),
    )

    logging_module.configure_logging()

    assert captured["loggers"] == {
        "celery.app.trace": {
            "handlers": ["default"],
            "level": "WARNING",
            "propagate": False,
        }
    }
