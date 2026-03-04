import logging


def configure_logging() -> None:
    app_logger = logging.getLogger("whoopmania")
    app_logger.setLevel(logging.INFO)

    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s"
            )
        )
        app_logger.addHandler(handler)

    app_logger.propagate = False
