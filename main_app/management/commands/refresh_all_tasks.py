import time
from django.core.management.base import BaseCommand
from main_app.models import BiddingTask
from main_app.avito_api import get_avito_access_token, get_item_info


class Command(BaseCommand):
    help = 'Обновляет title и image_url всех задач через Avito'

    def handle(self, *args, **options):
        tasks = BiddingTask.objects.select_related('avito_account').all()
        total = tasks.count()
        updated = 0
        errors = 0

        self.stdout.write(f"\nНайдено задач: {total}\n")

        token_cache = {}

        for i, task in enumerate(tasks, 1):
            account = task.avito_account
            if not account:
                self.stdout.write(self.style.WARNING(
                    f"  [{i}/{total}] Задача {task.id}: нет аккаунта"
                ))
                continue

            if account.pk not in token_cache:
                token = get_avito_access_token(
                    account.avito_client_id,
                    account.avito_client_secret
                )
                token_cache[account.pk] = token
            else:
                token = token_cache[account.pk]

            if not token:
                self.stdout.write(self.style.ERROR(
                    f"  [{i}/{total}] Задача {task.id}: нет токена"
                ))
                errors += 1
                continue

            info = get_item_info(token, task.ad_id)

            if info:
                changed = False

                if info.get("title") and info["title"] != task.title:
                    task.title = info["title"]
                    changed = True

                if info.get("image_url") and info["image_url"] != task.image_url:
                    task.image_url = info["image_url"]
                    changed = True

                if changed:
                    task.save(update_fields=["title", "image_url"])
                    updated += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  [{i}/{total}] OK {task.ad_id}: {task.title}"
                    ))
                else:
                    self.stdout.write(
                        f"  [{i}/{total}] -- {task.ad_id}: без изменений"
                    )
            else:
                errors += 1
                self.stdout.write(self.style.WARNING(
                    f"  [{i}/{total}] !! {task.ad_id}: нет данных"
                ))

            time.sleep(1)

        self.stdout.write(f"\nГотово! Обновлено: {updated}, ошибок: {errors}, всего: {total}")