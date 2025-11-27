# AutoPagi
אוטומציה של הורדת מידע מבנק פאגי.

## דרישות
- Python 3.10 ומעלה
- דפדפן כרום (Playwright מתקין גרסה תואמת לבד)

## התקנה
```bash
pip install -r requirements.txt
python -m playwright install
```

## הרצת סקריפט התחברות
הסקריפט `pagi_login.py` פותח את העמוד, לוחץ על "כניסה לחשבונך", ממלא שם משתמש וסיסמה ולוחץ "כניסה". ניתן להשאיר את הדפדפן פתוח לשליטה ידנית באמצעות דגל `--stay-open`.

```bash
python pagi_login.py --username <USER_CODE> --password <PASSWORD> --stay-open
```

ברירת המחדל היא ניווט ל-`https://www.pagi.co.il/private/`, אך ניתן להעביר קישור אחר באמצעות `--url`.
