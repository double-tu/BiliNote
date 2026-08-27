import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "services" / "model_capabilities.py"
spec = importlib.util.spec_from_file_location("model_capabilities", MODULE_PATH)
if spec is None or spec.loader is None:
    raise ImportError("model_capabilities module spec not found")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestModelCapabilities(unittest.TestCase):
    def test_known_openai_vision_models(self):
        for name in ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4-turbo"):
            caps = module.infer_model_capabilities(name, "openai")
            self.assertTrue(caps["supports_vision"], name)
            self.assertEqual(caps["model_role"], "vision")

    def test_glm_vision_model(self):
        caps = module.infer_model_capabilities("GLM-55V", "zhipu")
        self.assertTrue(caps["supports_vision"])

    def test_deepseek_is_text_only(self):
        caps = module.infer_model_capabilities("deepseek-chat", "deepseek")
        self.assertFalse(caps["supports_vision"])
        self.assertEqual(caps["model_role"], "text")

    def test_unknown_model_is_not_claimed_visual(self):
        caps = module.infer_model_capabilities("my-custom-model", "custom")
        self.assertIsNone(caps["supports_vision"])
        self.assertEqual(caps["confidence"], "unknown")


if __name__ == "__main__":
    unittest.main()
