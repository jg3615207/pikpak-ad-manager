import unittest
import os
import sqlite3
import shutil
import importlib

class TestAdManager(unittest.TestCase):
    def setUp(self):
        # Create a dummy rules file
        self.config_dir = "test_config"
        self.data_dir = "test_data"
        os.environ["CONFIG_DIR"] = self.config_dir
        os.environ["TARGET_DIR"] = self.data_dir
        os.environ["DB_PATH"] = os.path.join(self.config_dir, "test.db")
        os.environ["RULES_PATH"] = os.path.join(self.config_dir, "ads.json")
        os.environ["DRY_RUN"] = "False"
        
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Write dummy rules
        with open(os.environ["RULES_PATH"], "w") as f:
            f.write('{"videos": ["bad_ad.mp4"], "folders": ["bad_folder"]}')
            
        import main
        importlib.reload(main)
        # Avoid starting threads in test
        self.manager = main.manager

    def tearDown(self):
        # Clean up
        if hasattr(self, 'manager') and self.manager.db:
            self.manager.db.close()
        shutil.rmtree(self.config_dir)
        shutil.rmtree(self.data_dir)

    def test_is_ad(self):
        self.assertTrue(self.manager.is_ad("bad_ad.mp4"))
        self.assertTrue(self.manager.is_ad("BAD_AD.mp4"))
        self.assertTrue(self.manager.is_ad("bad_folder"))
        self.assertFalse(self.manager.is_ad("good_video.mp4"))

    def test_mtime_tracking(self):
        self.assertEqual(self.manager.get_last_mtime("/test/path"), -1)
        self.manager.update_mtime("/test/path", 12345.6)
        self.assertEqual(self.manager.get_last_mtime("/test/path"), 12345.6)

if __name__ == "__main__":
    unittest.main()
