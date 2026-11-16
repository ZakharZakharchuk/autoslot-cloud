import logging
import os

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

SERVICE_NAME = os.getenv("SERVICE_NAME", "local")
VERSION = os.getenv("APP_VERSION", "2.0.0")
