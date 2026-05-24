"""
Jinja2 模板引擎配置
分离以避免 app.py 与 routes 之间的循环导入
"""
from pathlib import Path
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
