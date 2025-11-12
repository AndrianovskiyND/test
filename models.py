import json
import os
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

class UserManager:
    def __init__(self, filename='users.json'):
        self.filename = filename
        self.users = self.load_users()
    
    def load_users(self):
        if os.path.exists(self.filename):
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            # Создаем начальных пользователей
            initial_users = {
                'admin': {
                    'password': generate_password_hash('admin123'),
                    'role': 'admin',
                    'name': 'Администратор'
                },
                'user': {
                    'password': generate_password_hash('user123'),
                    'role': 'worker',
                    'name': 'Работник'
                }
            }
            self.save_users(initial_users)
            return initial_users
    
    def save_users(self, users=None):
        if users is None:
            users = self.users
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    
    def add_user(self, username, password, role, name):
        if username in self.users:
            return False
        self.users[username] = {
            'password': generate_password_hash(password),
            'role': role,
            'name': name
        }
        self.save_users()
        return True
    
    def verify_user(self, username, password):
        user = self.users.get(username)
        if user and check_password_hash(user['password'], password):
            return user
        return None

class TaskManager:
    def __init__(self, filename='tasks.json'):
        self.filename = filename
        self.tasks = self.load_tasks()
        self.next_id = max([task['id'] for task in self.tasks]) + 1 if self.tasks else 1
    
    def load_tasks(self):
        if os.path.exists(self.filename):
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    
    def save_tasks(self):
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.tasks, f, ensure_ascii=False, indent=2)
    
    def generate_task_number(self):
        date_str = datetime.now().strftime("%y%m%d")
        today_tasks = [t for t in self.tasks if t['number'].startswith(f"TASK-{date_str}")]
        sequence = len(today_tasks) + 1
        return f"TASK-{date_str}-{sequence:04d}"
    
    def add_task(self, task_data):
        task = {
            'id': self.next_id,
            'number': self.generate_task_number(),
            'title': task_data['title'],
            'description': task_data['description'],
            'priority': task_data['priority'],
            'urgency': task_data['urgency'],
            'status': 'new',
            'progress': 0,
            'created_by': task_data['created_by'],
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'history': [{
                'action': 'Создана задача',
                'user': task_data['created_by'],
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'changes': {}
            }]
        }
        self.tasks.append(task)
        self.next_id += 1
        self.save_tasks()
        return task
    
    def update_task(self, task_id, updates, user):
        task = self.get_task(task_id)
        if not task:
            return None
        
        changes = {}
        for key, value in updates.items():
            if key in task and task[key] != value:
                changes[key] = {'from': task[key], 'to': value}
                task[key] = value
        
        if changes:
            task['history'].append({
                'action': 'Обновление задачи',
                'user': user,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'changes': changes
            })
            task['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.save_tasks()
        
        return task
    
    def get_task(self, task_id):
        return next((task for task in self.tasks if task['id'] == task_id), None)
    
    def add_comment(self, task_id, comment, user):
        task = self.get_task(task_id)
        if not task:
            return None
        
        if 'comments' not in task:
            task['comments'] = []
        
        task['comments'].append({
            'user': user,
            'text': comment,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        
        task['history'].append({
            'action': 'Добавлен комментарий',
            'user': user,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'changes': {'comment': comment[:50] + '...' if len(comment) > 50 else comment}
        })
        
        task['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.save_tasks()
        return task