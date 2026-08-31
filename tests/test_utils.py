from app.utils import safe_name
from app.source_providers import source_url
def test_safe_name(): assert safe_name(" hello world / x ")=="hello_world_x"; assert safe_name("../../")=="source"
def test_source_url(): assert source_url("chaturbate","demo")=="https://chaturbate.com/demo/"
