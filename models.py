import sqlite3
import json
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import os

class Database:
    def __init__(self, db_path='tasks.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица задач
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL,
                urgency TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER DEFAULT 0,
                created_by TEXT NOT NULL,
                assigned_to TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица истории задач
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                user TEXT NOT NULL,
                changes TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks (id)
            )
        ''')
        
        # Таблица комментариев
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS task_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                user TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks (id)
            )
        ''')
        
        # Создаем начальных пользователей
        cursor.execute('''
            INSERT OR IGNORE INTO users (username, password, role, name)
            VALUES (?, ?, ?, ?)
        ''', ('admin', generate_password_hash('admin123'), 'admin', 'Администратор'))
        
        cursor.execute('''
            INSERT OR IGNORE INTO users (username, password, role, name)
            VALUES (?, ?, ?, ?)
        ''', ('user', generate_password_hash('user123'), 'worker', 'Работник'))
        
        conn.commit()
        conn.close()

class UserManager:
    def __init__(self, db):
        self.db = db
    
    def verify_user(self, username, password):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user_data = cursor.fetchone()
        conn.close()
        
        if user_data and check_password_hash(user_data[2], password):
            return {
                'id': user_data[0],
                'username': user_data[1],
                'password': user_data[2],
                'role': user_data[3],
                'name': user_data[4]
            }
        return None
    
    def add_user(self, username, password, role, name):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO users (username, password, role, name)
                VALUES (?, ?, ?, ?)
            ''', (username, generate_password_hash(password), role, name))
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            conn.close()
            return False
    
    def get_all_users(self):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT id, username, role, name FROM users')
        users = cursor.fetchall()
        conn.close()
        
        return [{'id': u[0], 'username': u[1], 'role': u[2], 'name': u[3]} for u in users]
    
    def get_workers(self):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT username, name FROM users WHERE role = "worker"')
        workers = cursor.fetchall()
        conn.close()
        
        return [{'username': w[0], 'name': w[1]} for w in workers]

class TaskManager:
    def __init__(self, db):
        self.db = db
    
    def generate_task_number(self):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        date_str = datetime.now().strftime("%y%m%d")
        cursor.execute('''
            SELECT COUNT(*) FROM tasks 
            WHERE number LIKE ? AND date(created_at) = date('now')
        ''', (f'TASK-{date_str}-%',))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        sequence = count + 1
        return f"TASK-{date_str}-{sequence:04d}"
    
    def add_task(self, task_data):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        task_number = self.generate_task_number()
        
        cursor.execute('''
            INSERT INTO tasks (number, title, description, priority, urgency, status, progress, created_by, assigned_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            task_number,
            task_data['title'],
            task_data['description'],
            task_data['priority'],
            task_data['urgency'],
            'новая',
            0,
            task_data['created_by'],
            task_data.get('assigned_to')
        ))
        
        task_id = cursor.lastrowid
        
        # Добавляем запись в историю
        cursor.execute('''
            INSERT INTO task_history (task_id, action, user, changes)
            VALUES (?, ?, ?, ?)
        ''', (task_id, 'Задача создана', task_data['created_by'], '{}'))
        
        conn.commit()
        conn.close()
        
        return self.get_task(task_id)
    
    def get_all_tasks(self):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM tasks ORDER BY created_at DESC
        ''')
        
        tasks = cursor.fetchall()
        conn.close()
        
        return [self._dict_from_row(task) for task in tasks]
    
    def get_task(self, task_id):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
        task = cursor.fetchone()
        
        if not task:
            conn.close()
            return None
        
        task_dict = self._dict_from_row(task)
        
        # Получаем историю
        cursor.execute('SELECT * FROM task_history WHERE task_id = ? ORDER BY timestamp', (task_id,))
        history = cursor.fetchall()
        task_dict['history'] = [self._history_from_row(h) for h in history]
        
        # Получаем комментарии
        cursor.execute('SELECT * FROM task_comments WHERE task_id = ? ORDER BY timestamp', (task_id,))
        comments = cursor.fetchall()
        task_dict['comments'] = [self._comment_from_row(c) for c in comments]
        
        conn.close()
        return task_dict
    
    def update_task(self, task_id, updates, user):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # Получаем текущие данные
        cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
        current_task = self._dict_from_row(cursor.fetchone())
        
        changes = {}
        set_parts = []
        params = []
        
        for key, value in updates.items():
            if key in current_task and current_task[key] != value:
                changes[key] = {'from': current_task[key], 'to': value}
                set_parts.append(f"{key} = ?")
                params.append(value)
        
        if changes:
            params.append(task_id)
            cursor.execute(f'''
                UPDATE tasks SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', params)
            
            # Добавляем запись в историю
            cursor.execute('''
                INSERT INTO task_history (task_id, action, user, changes)
                VALUES (?, ?, ?, ?)
            ''', (task_id, 'Задача обновлена', user, json.dumps(changes, ensure_ascii=False)))
        
        conn.commit()
        conn.close()
        return self.get_task(task_id)
    
    def add_comment(self, task_id, comment, user):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO task_comments (task_id, user, text)
            VALUES (?, ?, ?)
        ''', (task_id, user, comment))
        
        # Добавляем запись в историю
        cursor.execute('''
            INSERT INTO task_history (task_id, action, user, changes)
            VALUES (?, ?, ?, ?)
        ''', (task_id, 'Добавлен комментарий', user, json.dumps({'comment': comment[:50] + '...' if len(comment) > 50 else comment}, ensure_ascii=False)))
        
        conn.commit()
        conn.close()
        return self.get_task(task_id)
    
    def _dict_from_row(self, row):
        return {
            'id': row[0],
            'number': row[1],
            'title': row[2],
            'description': row[3],
            'priority': row[4],
            'urgency': row[5],
            'status': row[6],
            'progress': row[7],
            'created_by': row[8],
            'assigned_to': row[9],
            'created_at': row[10],
            'updated_at': row[11]
        }
    
    def _history_from_row(self, row):
        return {
            'id': row[0],
            'task_id': row[1],
            'action': row[2],
            'user': row[3],
            'changes': json.loads(row[4]) if row[4] else {},
            'timestamp': row[5]
        }
    
    def _comment_from_row(self, row):
        return {
            'id': row[0],
            'task_id': row[1],
            'user': row[2],
            'text': row[3],
            'timestamp': row[4]
        }