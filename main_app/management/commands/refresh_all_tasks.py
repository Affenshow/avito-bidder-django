import time
import random
from django.core.management.base import BaseCommand
from django.db.models import Q
from main_app.models import BiddingTask
from main_app.avito_api import get_avito_access_token, get_item_info


class Command(BaseCommand):
    help = 'Обновляет title и image_url задач через Avito'

    def add_arguments(self, parser):
        parser.add_argument('--only-empty', action='store_true',
                            help='Только задачи без title/image')
        parser.add_argument('--pause', type=int, default=8,
                            help='Мин. пауза между запросами (сек)')

    def handle(self, *args, **options):
        only_empty = options['only_empty']
        base_pause = options['pause']

        tasks = BiddingTask.objects.select_related('avito_account').exclude(
            avito_account__isnull=True
        )

        if only_empty:
            tasks = tasks.filter(
                Q(title='') | Q(title__isnull=True) |
                Q(image_url='') | Q(image_url__isnull=True)
            )

        total = tasks.count()
        updated = 0
        errors = 0
        consecutive_fails = 0

        self.stdout.write(f"\nНайдено задач: {total}, пауза: {base_pause}с\n")

        token_cache = {}
        failed_ids = []

        for i, task in enumerate(tasks, 1):
            account = task.avito_account

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
                self.stdout.write(self.style.ERROR(
                    f"  [{i}/{total}] {task.ad_id}: нет токена"
                ))
                continue

            if consecutive_fails >= 3:
                wait = 60
                self.stdout.write(f"  ⏳ Много 429, ждём {wait}с...")
                time.sleep(wait)
                consecutive_fails = 0

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
                        f"  [{i}/{total}] ✅ {task.ad_id}: {task.title}"
                    ))
                else:
                    self.stdout.write(
                        f"  [{i}/{total}] -- {task.ad_id}: без изменений"
                    )
                consecutive_fails = 0
            else:
                errors += 1
                consecutive_fails += 1
                failed_ids.append(task.ad_id)
                self.stdout.write(self.style.WARNING(
                    f"  [{i}/{total}] ❌ {task.ad_id}: нет данных"
                ))

            pause = random.uniform(base_pause, base_pause + 5)
            time.sleep(pause)

        # Retry
        if failed_ids:
            wait = 120
            self.stdout.write(f"\n⏳ Повтор {len(failed_ids)} задач через {wait}с...")
            time.sleep(wait)

            retry_tasks = BiddingTask.objects.select_related(
                'avito_account'
            ).filter(ad_id__in=failed_ids)

            for i, task in enumerate(retry_tasks, 1):
                token = token_cache.get(task.avito_account.pk)
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
                        f"  [retry {i}/{len(failed_ids)}] ✅ {task.ad_id}"
                    ))
                else:
                    self.stdout.write(self.style.WARNING(
                        f"  [retry {i}/{len(failed_ids)}] ❌ {task.ad_id}"
                    ))

                time.sleep(random.uniform(15, 25))

        self.stdout.write(
            f"\n🏁 Обновлено: {updated}, ошибок: {errors}, всего: {total}"
        )