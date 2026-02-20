import time
import random
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
        skipped = 0

        self.stdout.write(f"\nНайдено задач: {total}\n")

        token_cache = {}
        failed_ids = []

        for i, task in enumerate(tasks, 1):
            account = task.avito_account
            if not account:
                self.stdout.write(self.style.WARNING(
                    f"  [{i}/{total}] Задача {task.id}: нет аккаунта"
                ))
                continue

            # Уже есть данные — пропускаем
            if task.title and task.image_url:
                skipped += 1
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
                errors += 1
                continue

            info = get_item_info(token, task.ad_id)

            if info and (info.get("title") or info.get("image_url")):
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
                    skipped += 1
            else:
                errors += 1
                failed_ids.append(task.ad_id)
                self.stdout.write(self.style.WARNING(
                    f"  [{i}/{total}] !! {task.ad_id}: нет данных"
                ))

            # Пауза 5-8 сек чтобы не словить 429
            pause = random.uniform(5, 8)
            time.sleep(pause)

        # --- Повторная попытка для проваленных (через 30 сек) ---
        if failed_ids:
            self.stdout.write(f"\nПовторная попытка для {len(failed_ids)} задач через 30 сек...")
            time.sleep(30)

            retry_tasks = BiddingTask.objects.select_related('avito_account').filter(
                ad_id__in=failed_ids
            )

            for i, task in enumerate(retry_tasks, 1):
                account = task.avito_account
                token = token_cache.get(account.pk)
                if not token:
                    continue

                info = get_item_info(token, task.ad_id)

                if info and (info.get("title") or info.get("image_url")):
                    if info.get("title"):
                        task.title = info["title"]
                    if info.get("image_url"):
                        task.image_url = info["image_url"]
                    task.save(update_fields=["title", "image_url"])
                    updated += 1
                    errors -= 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  [retry {i}/{len(failed_ids)}] OK {task.ad_id}: {task.title}"
                    ))
                else:
                    self.stdout.write(self.style.WARNING(
                        f"  [retry {i}/{len(failed_ids)}] !! {task.ad_id}: снова нет"
                    ))

                pause = random.uniform(8, 12)
                time.sleep(pause)

        self.stdout.write(
            f"\nГотово! Обновлено: {updated}, пропущено: {skipped}, "
            f"ошибок: {errors}, всего: {total}"
        )