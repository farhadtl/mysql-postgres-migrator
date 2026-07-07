# 🗄️ راهنمای جامع بکاپ و مدیریت دیتابیس PostgreSQL در Docker
این سند شامل دستورات ضروری برای تهیه بکاپ، بازگردانی (Restore) و انتقال دیتابیس `postgres_db` از کانتینر `new_postgres` است.

```powershell
# 1. ایجاد بکاپ در داخل کانتینر
docker exec -t new_postgres pg_dump -U postgres_user -d new_postgres -Fc -f /tmp/new_postgre_backup.dump

# 2. کپی فایل بکاپ از کانتینر به سیستم میزبان
docker cp new_postgres:/tmp/new_postgre_backup.dump ./new_postgre_backup.dump




# کپی فایل دامپ به داخل کانتینر مقصد
## اجرا در مسیر داکر اصلی پروژه
docker cp ./mysql-postgres-migrator/new_postgre_backup.dump your_postgres:/tmp/

# اجرای دستور ریستور
## اجرا در مسیر داکر اصلی پروژه 
docker exec -t your_postgres pg_restore -U sample_POSTGRES_USER -d sample_POSTGRES_USER -v --clean --if-exists /tmp/new_postgre_backup.dump