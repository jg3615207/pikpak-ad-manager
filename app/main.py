import os
import time
import json
import sqlite3
import shutil
import logging
from datetime import datetime
import threading
import difflib
import zhconv
import re
from flask import Flask, render_template, jsonify, request

# ---- Globals & Config ----
class MemoryHandler(logging.Handler):
    def __init__(self, capacity=100):
        super().__init__()
        self.capacity = capacity
        self.logs = []

    def emit(self, record):
        log_entry = self.format(record)
        self.logs.append(log_entry)
        if len(self.logs) > self.capacity:
            self.logs.pop(0)

log_handler = MemoryHandler(capacity=200)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

logger = logging.getLogger("PikPakAdManager")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logger.addHandler(stream_handler)

TARGET_DIR = os.environ.get("TARGET_DIR", "/data")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
DB_PATH = os.path.join(CONFIG_DIR, "scanner.db")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
RULES_PATH = os.path.join(CONFIG_DIR, "ads.json")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 300))
DRY_RUN = os.environ.get("DRY_RUN", "True").lower() in ("true", "1", "yes")

try:
    with open(SETTINGS_PATH, 'r') as f:
        _settings = json.load(f)
        if "target_dir" in _settings: TARGET_DIR = _settings["target_dir"]
        if "dry_run" in _settings: DRY_RUN = _settings["dry_run"]
        if "rules_path" in _settings: RULES_PATH = _settings["rules_path"]
except Exception:
    pass

def save_settings():
    try:
        with open(SETTINGS_PATH, 'w') as f:
            json.dump({"target_dir": TARGET_DIR, "dry_run": DRY_RUN, "rules_path": RULES_PATH}, f)
    except Exception as e:
        logger.error(f"Error saving settings: {e}")

os.makedirs(CONFIG_DIR, exist_ok=True)

# ---- Smart Detection Utils ----
def normalize_text(text):
    text = text.lower()
    text = zhconv.convert(text, 'zh-hans')
    text = re.sub(r'\s+', '', text)
    text = re.sub(r'\.(com|cc|net|org|xyz|site|fun|pw|la|vip|shop)', '', text)
    text = re.sub(r'\.(mp4|mkv|avi|wmv|jpg|png|gif|txt|zip|rar|apk|chm)', '', text)
    text = re.sub(r'\(\d+\)$', '', text)
    text = re.sub(r'_[a-z0-9]{4,6}$', '', text)
    return text

# ---- Backend Core ----
class AdManager:
    def __init__(self):
        self.ad_file_names = set()
        self.ad_folder_names = set()
        self.excludes = []
        self.raw_rules = {}
        self.scans_completed = 0
        self.items_deleted = 0
        self.status = "Idle"
        self.db = None
        self.scan_event = threading.Event()
        self.force_full_scan_next = False
        self.init_db()
        self.load_rules()

    def load_rules(self):
        if not os.path.exists(RULES_PATH):
            logger.warning(f"Rules file not found at {RULES_PATH}. Starting with empty rules.")
            self.raw_rules = {"videos": [], "images": [], "folders": [], "others": [], "excludes": []}
            return

        try:
            with open(RULES_PATH, 'r', encoding='utf-8') as f:
                self.raw_rules = json.load(f)
            self.ad_file_names.clear()
            self.ad_folder_names.clear()
            for category in ["videos", "images", "others"]:
                if category in self.raw_rules:
                    for item in self.raw_rules[category]:
                        self.ad_file_names.add(item.lower())
            if "folders" in self.raw_rules:
                for item in self.raw_rules["folders"]:
                    self.ad_folder_names.add(item.lower())
            self.excludes = [ex.lower() for ex in self.raw_rules.get("excludes", [])]
            logger.info(f"Loaded {len(self.ad_file_names)} file rules, {len(self.ad_folder_names)} folder rules, and {len(self.excludes)} excludes.")
        except Exception as e:
            logger.error(f"Error loading rules: {e}")
            self.raw_rules = {"videos": [], "images": [], "folders": [], "others": [], "excludes": []}

    def save_rules(self, new_rules):
        try:
            with open(RULES_PATH, 'w', encoding='utf-8') as f:
                json.dump(new_rules, f, indent=2, ensure_ascii=False)
            self.load_rules()
            return True
        except Exception as e:
            logger.error(f"Error saving rules: {e}")
            return False

    def init_db(self):
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = self.db.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS scanned_folders (path TEXT PRIMARY KEY, mtime REAL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS pending_review (path TEXT PRIMARY KEY, matched_rule TEXT, similarity REAL, name TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS known_safe (path TEXT PRIMARY KEY)")
        self.db.commit()

    def get_last_mtime(self, path):
        cursor = self.db.cursor()
        cursor.execute("SELECT mtime FROM scanned_folders WHERE path = ?", (path,))
        row = cursor.fetchone()
        return row[0] if row else -1

    def update_mtime(self, path, mtime):
        cursor = self.db.cursor()
        cursor.execute("INSERT INTO scanned_folders (path, mtime) VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime", (path, mtime))
        self.db.commit()

    def evaluate_file(self, path, name, is_dir):
        cursor = self.db.cursor()
        cursor.execute("SELECT path FROM known_safe WHERE path = ?", (path,))
        if cursor.fetchone():
            return ("SAFE", None, 0)
            
        target_rules = self.ad_folder_names if is_dir else self.ad_file_names
        
        name_lower = name.lower()
        if name_lower in target_rules:
            return ("EXACT", name_lower, 1.0)
            
        norm_name = normalize_text(name)
        best_match = None
        best_score = 0.0
        
        for rule in target_rules:
            norm_rule = normalize_text(rule)
            if norm_name == norm_rule:
                return ("FUZZY", rule, 0.99)
            if len(norm_rule) > 5 and norm_rule in norm_name:
                return ("FUZZY", rule, 0.95)
                
            score = difflib.SequenceMatcher(None, norm_name, norm_rule).ratio()
            if score > best_score:
                best_score = score
                best_match = rule
                
        if best_score >= 0.75:
            return ("FUZZY", best_match, best_score)
            
        return ("SAFE", None, best_score)

    def delete_item(self, path, is_dir):
        global DRY_RUN
        if DRY_RUN:
            logger.info(f"[DRY RUN] Would delete: {path}")
            self.items_deleted += 1
            return True
            
        try:
            if is_dir: shutil.rmtree(path)
            else: os.remove(path)
            logger.info(f"Deleted ad item: {path}")
            self.items_deleted += 1
            return True
        except Exception as e:
            logger.error(f"Failed to delete {path}: {e}")
            return False

    def scan_directory(self, current_dir):
        if not os.path.exists(current_dir): return

        try:
            dir_stat = os.stat(current_dir)
            current_mtime = dir_stat.st_mtime
            last_mtime = self.get_last_mtime(current_dir)
            if last_mtime >= current_mtime and last_mtime != -1:
                pass 
            else:
                logger.debug(f"Scanning changed folder: {current_dir}")

            with os.scandir(current_dir) as entries:
                for entry in entries:
                    is_excluded = False
                    entry_path_lower = entry.path.lower()
                    entry_name_lower = entry.name.lower()
                    for ex in self.excludes:
                        if ex in entry_name_lower or ex in entry_path_lower:
                            is_excluded = True
                            break
                    if is_excluded:
                        continue

                    status, rule, score = self.evaluate_file(entry.path, entry.name, entry.is_dir(follow_symlinks=False))
                    
                    if status == "EXACT":
                        self.delete_item(entry.path, entry.is_dir())
                        continue 
                    elif status == "FUZZY":
                        logger.info(f"Suspicious file flagged: {entry.name} (Matches: {rule} @ {score:.2f})")
                        cursor = self.db.cursor()
                        cursor.execute("INSERT OR IGNORE INTO pending_review (path, matched_rule, similarity, name) VALUES (?, ?, ?, ?)", 
                                       (entry.path, rule, score, entry.name))
                        self.db.commit()

                    if entry.is_dir(follow_symlinks=False):
                        self.scan_directory(entry.path)
            
            self.update_mtime(current_dir, current_mtime)

        except PermissionError:
            logger.warning(f"Permission denied accessing: {current_dir}")
        except Exception as e:
            logger.error(f"Error scanning {current_dir}: {e}")

    def run(self):
        logger.info(f"Starting Scanner. Target: {TARGET_DIR}")
        while True:
            self.status = "Scanning"
            if os.path.exists(TARGET_DIR):
                if self.force_full_scan_next:
                    logger.info("Forcing full scan! Clearing folder cache...")
                    self.db.execute("DELETE FROM scanned_folders")
                    self.db.commit()
                    self.force_full_scan_next = False
                    
                start_time = time.time()
                self.scan_directory(TARGET_DIR)
                self.scans_completed += 1
                logger.info(f"Scan completed in {time.time() - start_time:.2f}s.")
            else:
                logger.warning(f"Target directory {TARGET_DIR} not found.")
            
            self.status = "Idle"
            self.scan_event.clear()
            self.scan_event.wait(timeout=POLL_INTERVAL)

manager = AdManager()

# ---- Flask Web API ----
app = Flask(__name__)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    global DRY_RUN
    cursor = manager.db.cursor()
    cursor.execute("SELECT COUNT(*) FROM pending_review")
    pending_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM pending_review WHERE similarity >= 0.85")
    pending_high_count = cursor.fetchone()[0]
    
    return jsonify({
        "status": manager.status,
        "scans_completed": manager.scans_completed,
        "items_deleted": manager.items_deleted,
        "pending_count": pending_count,
        "pending_high_count": pending_high_count,
        "dry_run": DRY_RUN,
        "target_dir": TARGET_DIR,
        "rules_path": RULES_PATH
    })

@app.route('/api/logs', methods=['GET'])
def get_logs(): return jsonify({"logs": log_handler.logs})

@app.route('/api/rules', methods=['GET'])
def get_rules(): return jsonify(manager.raw_rules)

@app.route('/api/rules', methods=['POST'])
def update_rules():
    new_rules = request.json
    
    # Global deduplication
    seen = set()
    deduped_rules = {"videos": [], "images": [], "folders": [], "others": [], "excludes": []}
    
    for category in ["videos", "images", "folders", "others", "excludes"]:
        if category in new_rules:
            for item in new_rules[category]:
                item_lower = item.lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    deduped_rules[category].append(item)
                    
    if manager.save_rules(deduped_rules):
        return jsonify({"success": True})
    return jsonify({"success": False}), 500

from flask import send_file
@app.route('/api/rules/download', methods=['GET'])
def download_rules():
    if os.path.exists(RULES_PATH):
        return send_file(RULES_PATH, as_attachment=True, download_name='ads.json')
    return "Rules file not found", 404

@app.route('/api/scan/force', methods=['POST'])
def force_scan():
    manager.force_full_scan_next = True
    manager.scan_event.set()
    return jsonify({"success": True})

@app.route('/api/settings', methods=['POST'])
def update_settings():
    global DRY_RUN, TARGET_DIR, RULES_PATH
    data = request.json
    if 'dry_run' in data:
        DRY_RUN = data['dry_run']
        logger.info(f"Dry Run mode set to: {DRY_RUN}")
    if 'target_dir' in data:
        TARGET_DIR = data['target_dir']
        logger.info(f"Target Directory changed to: {TARGET_DIR}")
    if 'rules_path' in data:
        RULES_PATH = data['rules_path']
        logger.info(f"Rules Path changed to: {RULES_PATH}")
        manager.load_rules()
    save_settings()
    return jsonify({"success": True})

@app.route('/api/review', methods=['GET'])
def get_reviews():
    cursor = manager.db.cursor()
    cursor.execute("SELECT path, matched_rule, similarity, name FROM pending_review")
    items = [{"path": row[0], "matched_rule": row[1], "similarity": row[2], "name": row[3]} for row in cursor.fetchall()]
    return jsonify(items)

@app.route('/api/review/resolve', methods=['POST'])
def resolve_review():
    data = request.json
    path = data.get('path')
    action = data.get('action')
    name = data.get('name')
    
    cursor = manager.db.cursor()
    
    if action == "approve":
        # Delete file and remove from pending
        is_dir = os.path.isdir(path)
        manager.delete_item(path, is_dir)
        cursor.execute("DELETE FROM pending_review WHERE path = ?", (path,))
        # Add to rules list
        target_set = manager.ad_folder_names if is_dir else manager.ad_file_names
        if name and name.lower() not in target_set:
            if is_dir:
                if "folders" not in manager.raw_rules: manager.raw_rules["folders"] = []
                manager.raw_rules["folders"].insert(0, name)
            else:
                if "others" not in manager.raw_rules: manager.raw_rules["others"] = []
                manager.raw_rules["others"].insert(0, name)
            manager.save_rules(manager.raw_rules)
    elif action == "add_rule_only":
        cursor.execute("DELETE FROM pending_review WHERE path = ?", (path,))
        is_dir = os.path.isdir(path)
        target_set = manager.ad_folder_names if is_dir else manager.ad_file_names
        if name and name.lower() not in target_set:
            if is_dir:
                if "folders" not in manager.raw_rules: manager.raw_rules["folders"] = []
                manager.raw_rules["folders"].insert(0, name)
            else:
                if "others" not in manager.raw_rules: manager.raw_rules["others"] = []
                manager.raw_rules["others"].insert(0, name)
            manager.save_rules(manager.raw_rules)
    elif action == "reject":
        # Mark as safe, remove from pending
        cursor.execute("INSERT OR IGNORE INTO known_safe (path) VALUES (?)", (path,))
        cursor.execute("DELETE FROM pending_review WHERE path = ?", (path,))
        
    manager.db.commit()
    return jsonify({"success": True})

@app.route('/api/search', methods=['POST'])
def search_files():
    data = request.json
    query = data.get('query', '')
    if not query or not os.path.exists(TARGET_DIR): return jsonify([])
    
    results = []
    try:
        regex = re.compile(query, re.IGNORECASE)
        for root, dirs, files in os.walk(TARGET_DIR):
            for name in dirs + files:
                if regex.search(name):
                    results.append({"name": name, "path": os.path.join(root, name)})
                if len(results) >= 100: break # limit
            if len(results) >= 100: break
    except Exception as e:
        return jsonify({"error": str(e)}), 400
        
    return jsonify(results)

if __name__ == "__main__":
    scanner_thread = threading.Thread(target=manager.run, daemon=True)
    scanner_thread.start()
    app.run(host='0.0.0.0', port=5000)
