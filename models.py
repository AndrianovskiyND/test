import json
import os
import secrets
import shutil
import sqlite3
import string
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash


class MigrationManager:
    def __init__(self, db_path: str, conn: sqlite3.Connection):
        self.db_path = db_path
        self.conn = conn
        self.cursor = conn.cursor()
        self.pending_actions: List[Dict[str, Any]] = []
        self._columns_cache: Dict[str, List[str]] = {}

    def ensure_table(self, name: str, ddl: str) -> None:
        self.cursor.execute(
            'SELECT name FROM sqlite_master WHERE type = "table" AND name = ?',
            (name,),
        )
        exists = self.cursor.fetchone() is not None
        if not exists:
            self.cursor.execute(ddl)
            self.conn.commit()
            self._columns_cache.pop(name, None)

    def ensure_column(
        self,
        table: str,
        column: str,
        column_type: str,
        fill_expression: Optional[str] = None,
    ) -> None:
        columns = self._get_columns(table)
        if column in columns:
            return
        self.pending_actions.append(
            {
                'action': 'add_column',
                'table': table,
                'column': column,
                'column_type': column_type,
                'fill_expression': fill_expression,
            }
        )
        columns.append(column)

    def apply(self) -> None:
        if not self.pending_actions:
            return
        self._backup_database()
        for action in self.pending_actions:
            if action['action'] == 'add_column':
                table = action['table']
                column = action['column']
                column_type = action['column_type']
                fill_expression = action['fill_expression']
                self.cursor.execute(
                    f'ALTER TABLE {table} ADD COLUMN {column} {column_type}'
                )
                if fill_expression:
                    self.cursor.execute(
                        f'UPDATE {table} SET {column} = {fill_expression} WHERE {column} IS NULL'
                    )
        self.conn.commit()

    def _get_columns(self, table: str) -> List[str]:
        if table not in self._columns_cache:
            try:
                self.cursor.execute(f'PRAGMA table_info({table})')
                self._columns_cache[table] = [row[1] for row in self.cursor.fetchall()]
            except sqlite3.OperationalError:
                self._columns_cache[table] = []
        return self._columns_cache[table]

    def _backup_database(self) -> None:
        db_path = Path(self.db_path)
        if not db_path.exists():
            return
        backup_dir = db_path.parent / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        backup_path = backup_dir / f'{db_path.stem}_{timestamp}.bak'
        shutil.copy2(db_path, backup_path)


class Database:
    def __init__(self, db_path: str = 'tasks.db'):
        self.db_path = db_path
        self.init_db()

    def init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        migrator = MigrationManager(self.db_path, conn)

        migrator.ensure_table(
            'users',
            '''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''',
        )
        migrator.ensure_table(
            'tasks',
            '''
            CREATE TABLE tasks (
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
            ''',
        )
        migrator.ensure_table(
            'task_history',
            '''
            CREATE TABLE task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                user TEXT NOT NULL,
                changes TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks (id)
            )
            ''',
        )
        migrator.ensure_table(
            'task_comments',
            '''
            CREATE TABLE task_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                user TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks (id)
            )
            ''',
        )
        migrator.ensure_table(
            'system_settings',
            '''
            CREATE TABLE system_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''',
        )

        migrator.ensure_column('users', 'email', 'TEXT')
        migrator.ensure_column('users', 'updated_at', 'TIMESTAMP', 'CURRENT_TIMESTAMP')
        migrator.apply()
        
        # Инициализация настроек по умолчанию
        cursor = conn.cursor()
        default_settings = {
            'password_min_length': '6',
            'password_require_digits': 'false',
            'password_require_special': 'false',
        }
        for key, value in default_settings.items():
            cursor.execute(
                'INSERT OR IGNORE INTO system_settings (setting_key, setting_value) VALUES (?, ?)',
                (key, value)
            )
        conn.commit()

        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM users')
        user_count = cursor.fetchone()[0]
        if user_count == 0:
            password = self._generate_admin_password()
            cursor.execute(
                '''
                INSERT INTO users (username, password, role, name, email)
                VALUES (?, ?, ?, ?, ?)
                ''',
                ('admin', generate_password_hash(password), 'admin', 'Администратор', None),
            )
            conn.commit()
            print('=' * 60)
            print('Создан администратор по умолчанию:')
            print('  Логин: admin')
            print(f'  Пароль: {password}')
            print('Пожалуйста, войдите и смените пароль.')
            print('=' * 60)

        cursor.execute('DELETE FROM users WHERE username = ? AND role = ?', ('user', 'worker'))
        conn.commit()
        conn.close()

    @staticmethod
    def _generate_admin_password(length: int = 12) -> str:
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))


class UserManager:
    def __init__(self, db: Database):
        self.db = db
        self.settings_manager = SystemSettingsManager(db)

    def get_password_settings(self):
        return self.settings_manager.get_password_settings()

    def verify_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
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
                'name': user_data[4],
                'email': user_data[5],
            }
        return None

    def add_user(
        self,
        username: str,
        password: str,
        role: str,
        name: str,
        email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute(
                '''
                INSERT INTO users (username, password, role, name, email)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (username, generate_password_hash(password), role, name, email),
            )
            user_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return self.get_user_by_id(user_id)
        except sqlite3.IntegrityError:
            conn.close()
            return None

    def update_user_password(self, user_id: int, new_password: str) -> bool:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET password = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (generate_password_hash(new_password), user_id),
        )
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated

    def delete_user(self, user_id: int) -> bool:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted

    def update_user_profile(
        self,
        user_id: int,
        name: Optional[str] = None,
        email: Optional[str] = None,
        role: Optional[str] = None,
    ) -> bool:
        fields: List[str] = []
        params: List[Any] = []
        if name is not None:
            fields.append('name = ?')
            params.append(name)
        if email is not None:
            fields.append('email = ?')
            params.append(email)
        if role is not None:
            fields.append('role = ?')
            params.append(role)

        if not fields:
            return False

        params.append(user_id)
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            f'UPDATE users SET {", ".join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            params,
        )
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, username, role, name, email, created_at FROM users WHERE id = ?',
            (user_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            'id': row[0],
            'username': row[1],
            'role': row[2],
            'name': row[3],
            'email': row[4],
            'created_at': row[5],
        }

    def get_all_users(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT id, username, role, name, email, created_at FROM users ORDER BY id')
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                'id': row[0],
                'username': row[1],
                'role': row[2],
                'name': row[3],
                'email': row[4],
                'created_at': row[5],
            }
            for row in rows
        ]

    def get_workers(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT username, name, email FROM users WHERE role = "worker"')
        workers = cursor.fetchall()
        conn.close()

        return [{'username': w[0], 'name': w[1], 'email': w[2]} for w in workers]

    def get_assignable_users(self) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, role, name, email FROM users ORDER BY name COLLATE NOCASE')
        users = cursor.fetchall()
        conn.close()
        return [{'id': u[0], 'username': u[1], 'role': u[2], 'name': u[3], 'email': u[4]} for u in users]

    def user_name_exists(self, name: str) -> bool:
        if not name:
            return False
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM users WHERE name = ? LIMIT 1', (name,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists


    def get_assignable_users(self):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, role, name FROM users ORDER BY name COLLATE NOCASE')
        users = cursor.fetchall()
        conn.close()
        return [
            {'id': u[0], 'username': u[1], 'role': u[2], 'name': u[3]}
            for u in users
        ]

    def user_name_exists(self, name):
        if not name:
            return False
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM users WHERE name = ? LIMIT 1', (name,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

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
        return self.get_tasks_filtered(include_closed=True)

    def get_tasks_filtered(self, include_closed=False):
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        base_query = 'SELECT * FROM tasks'
        params = []
        if not include_closed:
            base_query += " WHERE status NOT IN (?, ?)"
            params.extend(['завершена', 'отменена'])
        base_query += ' ORDER BY datetime(created_at) DESC'
        cursor.execute(base_query, params)
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
        current_row = cursor.fetchone()
        if not current_row:
            conn.close()
            return None
        current_task = self._dict_from_row(current_row)
        
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
        if row is None:
            return None
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


class SystemSettingsManager:
    def __init__(self, db: Database):
        self.db = db

    def get_setting(self, key: str, default: str = '') -> str:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT setting_value FROM system_settings WHERE setting_key = ?', (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> bool:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO system_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (key, value),
        )
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated

    def get_all_settings(self) -> Dict[str, str]:
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT setting_key, setting_value FROM system_settings')
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}

    def get_password_settings(self) -> Dict[str, Any]:
        settings = self.get_all_settings()
        return {
            'min_length': int(settings.get('password_min_length', '6')),
            'require_digits': settings.get('password_require_digits', 'false').lower() == 'true',
            'require_special': settings.get('password_require_special', 'false').lower() == 'true',
        }