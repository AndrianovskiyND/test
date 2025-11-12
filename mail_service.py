import logging
import queue
import secrets
import smtplib
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Any, Dict, Iterable, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash


@dataclass
class EmailJob:
    recipients: List[str]
    subject: str
    body: str
    content_type: str = 'plain'


class _StaticSettingsManager:
    def __init__(self, config: Dict[str, Any]):
        self._config = config

    def get_settings(self) -> Dict[str, Any]:
        return self._config


class EmailSender:
    def __init__(self, settings_manager, logger: Optional[logging.Logger] = None, start_worker: bool = True):
        self.settings_manager = settings_manager
        self.logger = logger or logging.getLogger(__name__)
        self._queue: 'queue.Queue[EmailJob]' = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        if start_worker:
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    def send_async(self, recipients: Iterable[str], subject: str, body: str, content_type: str = 'plain') -> bool:
        recipients_list = [email for email in recipients if email]
        if not recipients_list:
            return False
        settings = self.settings_manager.get_settings()
        if not settings or not settings.get('verified'):
            return False
        self._queue.put(EmailJob(recipients=recipients_list, subject=subject, body=body, content_type=content_type))
        return True

    def send_immediate(self, settings: Dict[str, Any], recipients: Iterable[str], subject: str, body: str) -> None:
        recipients_list = [email for email in recipients if email]
        if not recipients_list:
            raise ValueError('Нет корректных адресов получения')
        with self._connect(settings) as smtp:
            message = self._build_message(settings, recipients_list, subject, body)
            smtp.sendmail(message['From'], recipients_list, message.as_string())

    def _worker_loop(self) -> None:
        while True:
            job: EmailJob = self._queue.get()
            try:
                settings = self.settings_manager.get_settings()
                if not settings or not settings.get('verified'):
                    continue
                with self._connect(settings) as smtp:
                    message = self._build_message(settings, job.recipients, job.subject, job.body, job.content_type)
                    smtp.sendmail(message['From'], job.recipients, message.as_string())
            except Exception as exc:  # noqa: BLE001
                self.logger.exception('Ошибка отправки email: %s', exc)
            finally:
                self._queue.task_done()

    @contextmanager
    def _connect(self, settings: Dict[str, Any]):
        host = settings.get('smtp_server')
        port = settings.get('smtp_port') or 25
        username = settings.get('username')
        password = settings.get('password')
        mode = (settings.get('encryption_mode') or 'auto').lower()
        use_ssl = bool(settings.get('use_ssl'))
        use_starttls = bool(settings.get('use_starttls'))

        if mode == 'ssl':
            use_ssl = True
            use_starttls = False
            auto_encryption = False
        elif mode == 'starttls':
            use_ssl = False
            use_starttls = True
            auto_encryption = False
        elif mode == 'none':
            use_ssl = False
            use_starttls = False
            auto_encryption = False
        else:
            auto_encryption = not use_ssl and not use_starttls

        if host is None:
            raise ValueError('Не задан SMTP сервер')

        if auto_encryption:
            if port == 465:
                use_ssl = True
            elif port == 587:
                use_starttls = True
            elif port == 25:
                use_ssl = False

        if use_ssl:
            smtp = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            try:
                smtp.ehlo()
            except smtplib.SMTPServerDisconnected:
                smtp.connect(host, port)
            if self._should_use_starttls(port, use_starttls, auto_encryption, smtp):
                context = ssl.create_default_context()
                smtp.starttls(context=context)
                smtp.ehlo()

        if username and password:
            smtp.login(username, password)

        try:
            yield smtp
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001
                smtp.close()

    def _should_use_starttls(
        self,
        port: int,
        explicit_starttls: bool,
        auto_encryption: bool,
        smtp: smtplib.SMTP,
    ) -> bool:
        if explicit_starttls:
            return True
        if auto_encryption and port in {587, 2525}:
            return True
        try:
            return auto_encryption and smtp.has_extn('starttls')
        except Exception:  # noqa: BLE001
            return False

    def _build_message(
        self,
        settings: Dict[str, Any],
        recipients: List[str],
        subject: str,
        body: str,
        content_type: str = 'plain',
    ) -> MIMEText:
        sender = settings.get('username') or settings.get('admin_email')
        if not sender:
            raise ValueError('Не указан адрес отправителя (username или admin_email)')
        message = MIMEText(body, content_type, 'utf-8')
        message['Subject'] = subject
        message['From'] = sender
        message['To'] = ', '.join(recipients)
        return message


def verify_email_config(smtp_config: Dict[str, Any], admin_email: str) -> str:
    config = dict(smtp_config)
    config['admin_email'] = admin_email
    config.setdefault('encryption_mode', (config.get('encryption_mode') or 'auto').lower())
    config.setdefault('use_ssl', bool(config.get('use_ssl')))
    config.setdefault('use_starttls', bool(config.get('use_starttls')))
    token = f'{secrets.randbelow(1_000_000):06d}'
    subject = 'Код подтверждения SMTP настроек'
    body = (
        'Здравствуйте!\n\n'
        'Для завершения настройки почтового сервера введите следующий код подтверждения: {token}\n\n'
        'Если вы не инициировали настройку, проигнорируйте это письмо.'
    ).format(token=token)
    transient_manager = _StaticSettingsManager({**config, 'verified': True})
    sender = EmailSender(transient_manager, start_worker=False)
    sender.send_immediate(config, [admin_email], subject, body)
    return token


class EmailService:
    def __init__(
        self,
        settings_manager,
        user_manager,
        task_manager,
        priority_labels: Dict[str, str],
        urgency_labels: Dict[str, str],
        status_labels: Dict[str, str],
        logger: Optional[logging.Logger] = None,
    ):
        self.settings_manager = settings_manager
        self.user_manager = user_manager
        self.task_manager = task_manager
        self.priority_labels = priority_labels
        self.urgency_labels = urgency_labels
        self.status_labels = status_labels
        self.logger = logger or logging.getLogger(__name__)
        self.sender = EmailSender(settings_manager, logger=self.logger)
        self._daily_scheduler_started = False

    def start_background_jobs(self) -> None:
        if not self._daily_scheduler_started:
            self._daily_scheduler_started = True
            threading.Thread(target=self._daily_digest_loop, daemon=True).start()

    def initiate_verification(self, config: Dict[str, Any]) -> None:
        if not config.get('admin_email'):
            admin_email = self.user_manager.get_admin_email()
            if admin_email:
                config['admin_email'] = admin_email
        if not config.get('admin_email'):
            raise ValueError('Укажите email администратора для верификации SMTP.')

        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        persist_config = {
            'smtp_server': config.get('smtp_server'),
            'smtp_port': config.get('smtp_port'),
            'username': config.get('username'),
            'password': config.get('password'),
            'admin_email': config.get('admin_email'),
            'use_ssl': bool(config.get('use_ssl')),
            'use_starttls': bool(config.get('use_starttls')),
            'encryption_mode': (config.get('encryption_mode') or 'auto').lower(),
            'token_created_at': now,
        }
        token = verify_email_config(persist_config, persist_config['admin_email'])
        token_hash = generate_password_hash(token)
        self.settings_manager.upsert_settings(persist_config, verification_token_hash=token_hash, verified=False)

    def verify_token(self, token: str) -> bool:
        settings = self.settings_manager.get_settings()
        if not settings or not settings.get('verification_token_hash'):
            return False
        if not check_password_hash(settings['verification_token_hash'], token):
            return False
        self.settings_manager.mark_verified()
        self.logger.info('SMTP настройки успешно подтверждены')
        return True

    def notify_task_created(self, task: Dict[str, Any]) -> None:
        recipients = self.user_manager.get_all_emails()
        if not recipients:
            return
        subject = f"Новая задача {task['number']}: {task['title']}"
        body_lines = [
            'Создана новая задача.',
            f"Номер: {task['number']}",
            f"Название: {task['title']}",
            f"Описание: {task['description']}",
            f"Важность: {self.priority_labels.get(task['priority'], task['priority'])}",
            f"Срочность: {self.urgency_labels.get(task['urgency'], task['urgency'])}",
            f"Статус: {self.status_labels.get(task['status'], task['status'])}",
            f"Создал: {task['created_by']}",
        ]
        if task.get('assigned_to'):
            body_lines.append(f"Ответственный: {task['assigned_to']}")
        body = '\n'.join(body_lines)
        self.sender.send_async(recipients, subject, body)

    def notify_task_status_change(self, task: Dict[str, Any], previous_status: Optional[str]) -> None:
        recipients: List[str] = []
        creator = self.user_manager.get_user_by_name(task.get('created_by', '')) if task.get('created_by') else None
        if creator and creator.get('email'):
            recipients.append(creator['email'])
        if task.get('assigned_to'):
            assignee = self.user_manager.get_user_by_name(task['assigned_to'])
            if assignee and assignee.get('email'):
                recipients.append(assignee['email'])
        recipients = list(dict.fromkeys(recipients))
        if not recipients:
            return

        subject = f"Обновление задачи {task['number']}"
        body = (
            'Статус задачи был изменён.\n\n'
            f"Номер: {task['number']}\n"
            f"Название: {task['title']}\n"
            f"Прошлый статус: {self.status_labels.get(previous_status, previous_status)}\n"
            f"Новый статус: {self.status_labels.get(task['status'], task['status'])}\n"
            f"Текущий прогресс: {task['progress']}%\n"
        )
        self.sender.send_async(recipients, subject, body)

    def send_daily_reminder(self) -> None:
        tasks = self.task_manager.get_unassigned_active_tasks()
        if not tasks:
            return
        recipients = self.user_manager.get_all_emails()
        if not recipients:
            return

        lines = ['Напоминание: есть задачи без ответственных.']
        for item in tasks:
            lines.append(
                f"- {item['number']} — {item['title']} (создал {item['created_by']}, статус: {self.status_labels.get(item['status'], item['status'])})"
            )
        body = '\n'.join(lines)
        subject = 'Напоминание о невзятых задачах'
        self.sender.send_async(recipients, subject, body)

    def _daily_digest_loop(self) -> None:
        while True:
            now = datetime.now()
            next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            wait_seconds = max(5, int((next_run - now).total_seconds()))
            time.sleep(wait_seconds)
            try:
                self.send_daily_reminder()
            except Exception as exc:  # noqa: BLE001
                self.logger.exception('Ошибка при отправке ежедневного напоминания: %s', exc)

