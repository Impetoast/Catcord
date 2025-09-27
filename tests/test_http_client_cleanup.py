import asyncio
import importlib.util
import sys
import types
import pathlib
from types import SimpleNamespace
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_cog(module_name: str):
    if "cogs" not in sys.modules:
        pkg = types.ModuleType("cogs")
        pkg.__path__ = [str(ROOT / "cogs")]
        sys.modules["cogs"] = pkg

    full_name = f"cogs.{module_name}"
    path = ROOT / "cogs" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(full_name, path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "cogs"
    sys.modules[full_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)  # type: ignore[assignment]
    return module


class AsyncClientCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_autotranslate_client_closed_on_unload(self):
        module = _load_cog("autotranslate")
        AutoTranslate = module.AutoTranslate
        loop = asyncio.get_running_loop()
        bot = SimpleNamespace(loop=loop)

        cog = AutoTranslate(bot)
        self.assertFalse(cog._deepl_client.is_closed)

        cog.cog_unload()
        await asyncio.sleep(0)

        self.assertTrue(cog._deepl_client.is_closed)

    async def test_translate_client_closed_on_unload(self):
        module = _load_cog("translate")
        Translate = module.Translate
        loop = asyncio.get_running_loop()
        bot = SimpleNamespace(loop=loop)

        cog = Translate(bot)
        client = cog._http_client
        self.assertFalse(client.is_closed)

        cog.cog_unload()
        await asyncio.sleep(0)

        self.assertTrue(client.is_closed)
        task = getattr(cog, "_language_task", None)
        if task is not None:
            self.assertTrue(task.done())

    async def test_langrelay_clients_closed_on_unload(self):
        module = _load_cog("langrelay")
        LangRelay = module.LangRelay
        loop = asyncio.get_running_loop()
        bot = SimpleNamespace(loop=loop)

        cog = LangRelay(bot)
        deepl_client = cog._deepl_client
        lang_client = cog._deepl_lang_client
        openai_client = cog._openai_client

        self.assertFalse(deepl_client.is_closed)
        self.assertFalse(lang_client.is_closed)
        self.assertFalse(openai_client.is_closed)

        cog.cog_unload()
        await asyncio.sleep(0)

        self.assertTrue(deepl_client.is_closed)
        self.assertTrue(lang_client.is_closed)
        self.assertTrue(openai_client.is_closed)
