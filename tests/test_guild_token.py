import os
import importlib
import unittest


class GuildTokenTest(unittest.TestCase):
    def test_is_allowed_guild(self):
        os.environ['DISCORD_TOKEN'] = 'x'
        os.environ['GUILD_ID'] = '123'
        main = importlib.reload(importlib.import_module('main'))
        self.assertTrue(main.is_allowed_guild(123))
        self.assertFalse(main.is_allowed_guild(456))
        self.assertFalse(main.is_allowed_guild(None))


if __name__ == '__main__':
    unittest.main()
