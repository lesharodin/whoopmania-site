# backend/app/core/config.py

class Settings:
    def __init__(self):
        self.PROJECT_NAME = "WhoopMania"
        # Путь к папке с шаблонами Jinja
        self.TEMPLATE_DIR = "backend/app/templates"


# создаём единственный экземпляр настроек
_settings = Settings()


def get_settings() -> Settings:
    return _settings
