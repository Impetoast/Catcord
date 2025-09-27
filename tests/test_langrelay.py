import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock


package_path = pathlib.Path(__file__).resolve().parents[1] / "cogs"
if "cogs" not in sys.modules:
    pkg = types.ModuleType("cogs")
    pkg.__path__ = [str(package_path)]
    sys.modules["cogs"] = pkg

spec = importlib.util.spec_from_file_location("cogs.langrelay", package_path / "langrelay.py")
langrelay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(langrelay)
LangRelay = langrelay.LangRelay


class FakeChannel:
    def __init__(self, channel_id: int, name: str):
        self.id = channel_id
        self.name = name
        self.mention = f"<#{channel_id}>"


class FakeGuild:
    def __init__(self, guild_id: int, name: str, channels: list[FakeChannel]):
        self.id = guild_id
        self.name = name
        self.text_channels = channels

    def get_channel(self, channel_id: int):
        for channel in self.text_channels:
            if channel.id == channel_id:
                return channel
        return None


class LangRelayMigrationTest(unittest.TestCase):
    def setUp(self):
        self._orig_data_dir = langrelay.DATA_DIR
        self._tmp_dir = tempfile.TemporaryDirectory()
        langrelay.DATA_DIR = pathlib.Path(self._tmp_dir.name)
        langrelay.DATA_DIR.mkdir(parents=True, exist_ok=True)

        self.bot = mock.Mock()
        self.cog = LangRelay(self.bot)

        self.channel = FakeChannel(111, "general")
        self.guild = FakeGuild(222, "Guild", [self.channel])
        self.bot.get_guild.return_value = self.guild

    def tearDown(self):
        langrelay.DATA_DIR = self._orig_data_dir
        self._tmp_dir.cleanup()

    def test_ensure_blocks_migrates_channel_names(self):
        cfg = {"groups": {"default": {self.channel.name: "EN"}}}
        self.cog._ensure_blocks(cfg, self.guild)
        self.assertEqual(cfg["groups"]["default"], {str(self.channel.id): "EN"})

    def test_load_guild_persists_migrated_structure(self):
        path = langrelay.DATA_DIR / f"{self.guild.id}.json"
        path.write_text(
            json.dumps(
                {
                    "provider": "deepl",
                    "options": {"enabled": True},
                    "groups": {"default": {self.channel.name: "EN"}},
                    "group_options": {},
                }
            ),
            encoding="utf-8",
        )

        self.cog._load_guild(self.guild)
        stored = self.cog.guild_config[self.guild.id]["groups"]["default"]
        self.assertEqual(stored, {str(self.channel.id): "EN"})

        file_data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(file_data["groups"]["default"], {str(self.channel.id): "EN"})

    def test_channel_display_handles_missing_targets(self):
        self.cog._guild_channel_cache[self.guild.id] = {self.channel.id: self.channel}

        mention = self.cog._channel_display(self.guild, str(self.channel.id))
        self.assertEqual(mention, self.channel.mention)

        missing_id = self.cog._channel_display(self.guild, "999")
        self.assertEqual(missing_id, "<#999>")

        missing_name = self.cog._channel_display(self.guild, "not-here")
        self.assertEqual(missing_name, "#not-here")
