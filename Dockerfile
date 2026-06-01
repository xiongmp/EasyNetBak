FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN python -c "from pathlib import Path; import re; path = Path('/etc/ssl/openssl.cnf'); text = path.read_text(); updated = re.sub(r'(?m)^\\s*CipherString\\s*=.*$', 'CipherString = DEFAULT@SECLEVEL=1', text, count=1); updated = updated if updated != text else text.rstrip() + '\n\n# Legacy SSH compatibility for older network devices\nCipherString = DEFAULT@SECLEVEL=1\n'; path.write_text(updated)"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

COPY app ./app
COPY alembic.ini .
COPY migrations ./migrations

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

