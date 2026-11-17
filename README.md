# Hệ thống Quản lý Đào tạo, Thi và Cấp chứng chỉ (CCE)

**Trung tâm Kỹ thuật Điện toán — Trường Đại học Bách Khoa, ĐHQG-HCM**

Giao diện hiện đại, chuyên nghiệp được xây dựng trên nền tảng Django 5.2 và django-unfold (Concept 3 – Chính Xác).

---

## ⚡ Chạy dự án lần đầu

Làm lần lượt các bước dưới đây để cài đặt, tạo database, nạp dữ liệu mẫu và khởi động hệ thống.

### Windows (PowerShell)

**Chuẩn bị:**

Cài Git và Python 3.10 trở lên (khuyến nghị Python 3.12). Khi cài Python, chọn **Add Python to PATH**.

Mở PowerShell và kiểm tra:

```powershell
git --version
python --version
```

**1. Tải mã nguồn về máy:**

```powershell
git clone https://github.com/congphuc-cyber/CCE.git
cd CCE
```

**2. Cài thư viện, tạo database và nạp dữ liệu dùng thử:**

```powershell
.\scripts\setup.ps1 -Demo
```

Chờ lệnh chạy xong. Script tự tạo môi trường Python `.venv`, cài thư viện, tạo database, áp dụng migrations, tạo tài khoản quản trị và nạp dữ liệu mẫu.

Nếu sử dụng PostgreSQL, hãy khởi động PostgreSQL, tạo role/database và thiết lập các biến `DB_*` theo mục **PostgreSQL và nhập bộ dữ liệu mẫu** trước khi chạy setup.

Django không tự động đọc các biến database từ file `.env`.

**3. Khởi động ứng dụng:**

```powershell
.\run.ps1
```

Mở địa chỉ sau trên trình duyệt:

http://127.0.0.1:8001/

Đăng nhập bằng tài khoản quản trị đã được cấu hình trong môi trường của bạn. Không chia sẻ thông tin đăng nhập trong tài liệu hoặc log.

Nhấn `Ctrl + C` trong PowerShell để dừng ứng dụng.

Nếu PowerShell báo không cho phép chạy script, thực hiện lệnh sau trong cùng cửa sổ rồi chạy lại bước 2:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Linux / macOS

Đảm bảo đã cài Git và Python 3.10 trở lên.

Trên Ubuntu/Debian, nếu chưa có Python hoặc hỗ trợ `venv`, cài bằng:

```bash
sudo apt install git python3 python3-venv python3-pip
```

Sau đó chạy trong Terminal:

```bash
git clone https://github.com/congphuc-cyber/CCE.git
cd CCE
chmod +x scripts/setup.sh scripts/run.sh
./scripts/setup.sh --demo
./scripts/run.sh
```

Mở http://127.0.0.1:8001/ và đăng nhập bằng tài khoản quản trị đã cấu hình cho môi trường của bạn.

> Muốn chỉ tạo database và tài khoản quản trị mà không nạp dữ liệu mẫu, bỏ `-Demo` trên Windows hoặc `--demo` trên Linux/macOS.

### Chạy bằng Docker (tùy chọn)

Nếu đã cài Docker và Docker Compose, chạy:

```powershell
git clone https://github.com/congphuc-cyber/CCE.git
cd CCE
docker compose up --build
```

Compose tự khởi tạo database và dữ liệu demo theo cấu hình của dự án.

Mở http://127.0.0.1:8001/ để truy cập hệ thống.

Nhấn `Ctrl + C` để dừng ứng dụng.

---

## 🌐 Địa chỉ truy cập

Máy chủ phát triển mặc định lắng nghe tại:

`http://127.0.0.1:8001/`

| Khu vực | Đường dẫn URL | Mô tả |
|---|---|---|
| Trang chủ | `/` | Cổng thông tin CCE dành cho công chúng và thí sinh |
| Trang chủ (đường dẫn thay thế) | `/home/` | Hiển thị cùng trang chủ CCE |
| Đăng nhập | `/login/` | Cổng đăng nhập phân quyền cho cán bộ, giáo viên |
| Khu vực Quản trị | `/admin/` | Trang quản trị hiện đại (Unfold Admin) |
| Bảng điều khiển | `/dashboard/` | Chỉ số hoạt động và biểu đồ dành cho Giáo vụ |
| Kiểm tra giao diện | `/ui-demo/` | Showcase các thành phần Design Tokens (khi DEBUG=True) |

---

## 🛠 Yêu cầu hệ thống

- **Hệ điều hành:** Windows 10/11, macOS hoặc Linux.
- **Python:** Phiên bản `>= 3.10` (khuyến nghị Python 3.12).
- **Database:** SQLite cho môi trường phát triển hoặc PostgreSQL.
- **Trình duyệt:** Chrome, Edge, Firefox hoặc Safari.
- **Docker và Docker Compose:** Tùy chọn nếu chạy bằng container.

---

## 📁 Cấu trúc thư mục dự án

```text
CCE/
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── requirements/
│   ├── base.txt
│   ├── dev.txt
│   └── prod.txt
├── scripts/
│   ├── setup.ps1
│   ├── run.ps1
│   ├── setup.sh
│   ├── run.sh
│   └── entrypoint.sh
└── site1/
    ├── manage.py
    ├── site1/
    │   ├── urls.py
    │   ├── wsgi.py
    │   ├── asgi.py
    │   └── settings/
    │       ├── __init__.py
    │       ├── base.py
    │       ├── dev.py
    │       └── prod.py
    └── training/
        ├── models.py
        ├── views.py
        ├── forms.py
        ├── admin.py
        ├── access.py
        ├── audit.py
        ├── navigation.py
        ├── context_processors.py
        ├── templatetags/
        ├── management/
        │   └── commands/
        │       ├── bootstrap.py
        │       ├── create_cce_roles.py
        │       └── seed_demo_data.py
        ├── templates/
        └── static/
            └── training/
                └── css/
```

---

## ⚙️ Danh sách biến môi trường

| Tên biến | Mặc định (Dev) | Mô tả |
|---|---|---|
| `DJANGO_SECRET_KEY` | Khóa phát triển local | Khóa bí mật Django. Bắt buộc sử dụng giá trị riêng, đủ mạnh trên Production. |
| `DJANGO_ENV` | `development` | Môi trường chạy ứng dụng. |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` | Danh sách hostname được phép truy cập, phân cách bằng dấu phẩy. |
| `PORT` | `8001` | Cổng máy chủ web lắng nghe. Render tự cấp biến này khi chạy Web Service. |
| `DATABASE_URL` | Không bắt buộc ở local | Chuỗi kết nối PostgreSQL, sử dụng khi triển khai trên Render. |
| `DB_NAME` | Tùy cấu hình PostgreSQL | Tên database PostgreSQL. |
| `DB_USER` | Tùy cấu hình PostgreSQL | Role PostgreSQL Django sử dụng để kết nối. |
| `DB_PASSWORD` | Bắt buộc khi dùng cấu hình `DB_*` | Mật khẩu PostgreSQL. |
| `DB_HOST` | `127.0.0.1` | Máy chủ PostgreSQL. |
| `DB_PORT` | `5432` | Cổng PostgreSQL. |

**Lưu ý bảo mật:**

- Không đưa mật khẩu database, secret key hoặc thông tin đăng nhập thật vào GitHub.
- Các giá trị bí mật cần được thiết lập trong môi trường chạy ứng dụng.
- Trên Render, sử dụng Environment Variables để lưu thông tin cấu hình.

---

## 🐘 PostgreSQL và nhập bộ dữ liệu mẫu

### Cấu hình kết nối PostgreSQL ở môi trường local

Django lấy cấu hình database trong `site1/site1/settings/base.py`.

Khi không có `DATABASE_URL`, dự án sử dụng các biến môi trường `DB_*`:

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "cce_training"),
        "USER": os.environ.get("DB_USER", "cce_app"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
```

Đặt các biến môi trường trong cùng cửa sổ PowerShell dùng để chạy lệnh Django:

```powershell
$env:DB_NAME = "cce_training"
$env:DB_USER = "cce_app"
$env:DB_PASSWORD = "<mat-khau-PostgreSQL>"
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "5432"
```

Thay `<mat-khau-PostgreSQL>` bằng mật khẩu PostgreSQL của bạn. Không commit mật khẩu thật vào repository.

### Tạo role và database

Mở Query Tool trên database `postgres` trong pgAdmin và chạy:

```sql
CREATE ROLE cce_app LOGIN PASSWORD '<mat-khau-PostgreSQL>';
```

Sau đó chạy riêng câu lệnh:

```sql
CREATE DATABASE cce_training OWNER cce_app;
```

> Chạy hai câu lệnh riêng biệt. `CREATE DATABASE` không được chạy bên trong transaction.

### Áp dụng migrations và kiểm tra kết nối

Từ thư mục gốc dự án, cài dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements\base.txt
```

Sau đó:

```powershell
cd .\site1
& '..\.venv\Scripts\python.exe' -X utf8 manage.py migrate
& '..\.venv\Scripts\python.exe' -X utf8 manage.py check --database default
```

### Kết nối PostgreSQL trên Render bằng DATABASE_URL

Khi triển khai trên Render, dự án sử dụng một biến môi trường duy nhất là `DATABASE_URL`.

Thực hiện:

1. Tạo PostgreSQL Database trên Render.
2. Mở phần Connections của database.
3. Sao chép **Internal Database URL**.
4. Mở Web Service của dự án.
5. Vào mục Environment.
6. Thêm biến:

| Key | Value |
|---|---|
| `DATABASE_URL` | Internal Database URL vừa sao chép |

Không công khai giá trị `DATABASE_URL` vì chuỗi này chứa thông tin xác thực database.

Khi `DATABASE_URL` được thiết lập, Django sẽ sử dụng chuỗi kết nối này thay cho các biến `DB_*`.

---

## 📦 Chuyển dữ liệu hiện có từ SQLite (nếu cần)

Nếu muốn giữ dữ liệu trong `site1/db.sqlite3`, hãy sao lưu và xuất fixture khi Django đang sử dụng SQLite.

Tạm thời thay cấu hình `DATABASES` trong `site1/site1/settings/base.py` bằng:

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
```

Sau đó chạy từ thư mục gốc dự án:

```powershell
cd .\site1
Copy-Item .\db.sqlite3 .\db.sqlite3.bak
$env:PYTHONUTF8 = "1"

& '..\.venv\Scripts\python.exe' -X utf8 manage.py dumpdata --natural-foreign --natural-primary `
    --exclude contenttypes --exclude auth.permission --exclude admin.logentry `
    --exclude sessions --indent 2 --output .\sqlite_export.json
```

Tiếp theo khôi phục cấu hình PostgreSQL trong `base.py`, thiết lập các biến `DB_*` hoặc `DATABASE_URL`, rồi chạy:

```powershell
& '..\.venv\Scripts\python.exe' -X utf8 manage.py migrate
& '..\.venv\Scripts\python.exe' -X utf8 manage.py loaddata .\sqlite_export.json
```

`dumpdata` và `loaddata` phải chạy ở hai thời điểm khác nhau: lần đầu trên SQLite, lần sau trên PostgreSQL.

Nếu chỉ cần database PostgreSQL mới để nhập dữ liệu mẫu, có thể bỏ qua bước chuyển fixture này.

---

## 📥 Nhập bộ dữ liệu mẫu

Lệnh `training/management/commands/import_sample_bulk_data.py` đọc các workbook Excel trong `sample_bulk_data` và nhập dữ liệu thông qua Django models.

Lệnh cần được chạy sau khi đã áp dụng migrations trên database đích.

Từ PowerShell đã cấu hình database:

```powershell
cd .\site1
$env:PYTHONUTF8 = "1"
```

Kiểm tra dữ liệu và quan hệ trước khi nhập:

```powershell
& '..\.venv\Scripts\python.exe' -X utf8 manage.py import_sample_bulk_data --dry-run
```

Chỉ chạy lệnh nhập sau khi kiểm tra thành công:

```powershell
& '..\.venv\Scripts\python.exe' -X utf8 manage.py import_sample_bulk_data
```

Mặc định lệnh đọc thư mục `..\sample_bulk_data`.

Nếu bộ dữ liệu nằm ở thư mục khác, truyền đường dẫn:

```powershell
& '..\.venv\Scripts\python.exe' -X utf8 manage.py import_sample_bulk_data `
    --source "C:\duong-dan\sample_bulk_data" --dry-run
```

Lệnh chạy trong transaction. Các bản ghi khớp dữ liệu mẫu được bỏ qua khi chạy lại; bản ghi xung đột sẽ được báo lỗi để tránh ghi đè dữ liệu hiện có.

---

## 🧪 Cách chạy kiểm thử (Tests)

Hệ thống đi kèm bộ kiểm thử tự động để kiểm tra phân quyền, điều hướng, logic lớp học, nhật ký thao tác và xác thực.

Chạy toàn bộ test:

```bash
cd site1
python manage.py test training
```

---

## 📦 Lệnh tạo dữ liệu mẫu (Demo Data)

Để khởi tạo dữ liệu mẫu:

```bash
cd site1
python manage.py bootstrap --demo
```

Để tạo dữ liệu kiểm thử cho các luồng kết quả cuối khóa, chứng nhận/chứng chỉ, lịch sử chuyển lớp và tra cứu công khai:

```bash
python manage.py seed_workflow_test_data
```

Lệnh tạo dữ liệu kiểm thử nên được chạy trong môi trường phát triển (`DEBUG=True`).

Dữ liệu sử dụng tên tổng hợp và email thuộc miền dành riêng `example.test`, không phải hồ sơ thật.

---

## 🚀 Triển khai Production trên Render

Dự án hỗ trợ triển khai bằng Docker trên Render.

### 1. Chuẩn bị PostgreSQL

- Tạo PostgreSQL Database trên Render.
- Chọn region Singapore để phù hợp với Web Service.
- Sao chép Internal Database URL.
- Lưu URL để thiết lập biến `DATABASE_URL` ở bước tiếp theo.

### 2. Tạo Web Service

1. Đăng nhập Render.
2. Chọn **New + → Web Service**.
3. Kết nối repository GitHub `congphuc-cyber/CCE`.
4. Chọn branch muốn triển khai.
5. Chọn môi trường Docker.
6. Chọn region Singapore.
7. Tạo Web Service.

Render sẽ sử dụng `Dockerfile` ở thư mục gốc để build ứng dụng.

### 3. Thiết lập Environment Variables

Trong Web Service, mở mục **Environment** và thiết lập:

| Key | Value |
|---|---|
| `DATABASE_URL` | Internal Database URL của PostgreSQL Render |
| `DJANGO_SECRET_KEY` | Một Django secret key riêng, đủ mạnh |
| `ALLOWED_HOSTS` | Tên miền Web Service, ví dụ `ten-dich-vu.onrender.com` |

Không thêm `https://` vào giá trị `ALLOWED_HOSTS`.

Render tự cấp biến `PORT`. Không cần tự đặt biến này.

### 4. Khởi động ứng dụng

Dockerfile sử dụng cấu hình `site1.settings.prod`.

Entrypoint sẽ tự động:

1. Chạy database migrations.
2. Tạo bảng cache nếu chưa tồn tại.
3. Thu thập static files.
4. Khởi chạy ứng dụng bằng Gunicorn.

WhiteNoise được sử dụng để phục vụ static files trong môi trường Production.

Sau khi deploy thành công, truy cập URL Render cấp cho Web Service.

**Lưu ý:**

- Không đưa `DATABASE_URL` hoặc `DJANGO_SECRET_KEY` lên GitHub.
- Không sử dụng tài khoản hoặc mật khẩu demo công khai trên môi trường Production.
- Database Production cần được sao lưu định kỳ.
- File upload lưu trên filesystem của container có thể mất khi dịch vụ được triển khai lại hoặc thay thế instance. Nếu cần lưu trữ lâu dài, nên sử dụng dịch vụ lưu trữ file riêng.

---

## ❓ Xử lý sự cố thường gặp (Troubleshooting)

**1. Lỗi `No module named unfold`**

Giải pháp: Đảm bảo đã kích hoạt môi trường ảo `.venv` hoặc cài dependencies:

```powershell
pip install -r requirements/base.txt
```

**2. Lỗi cổng 8001 bị chiếm dụng (`Address already in use`)**

Giải pháp: Chạy trên cổng khác, ví dụ:

```powershell
.\scripts\run.ps1 8002
```

Hoặc trên Linux/macOS:

```bash
./scripts/run.sh 8002
```

**3. Thay đổi mật khẩu tài khoản quản trị**

Sử dụng Django management command:

```bash
python manage.py changepassword <username>
```

**4. Lỗi kết nối PostgreSQL**

Kiểm tra:

- PostgreSQL đã được khởi động chưa.
- Tên database và role có chính xác không.
- Mật khẩu kết nối có đúng không.
- Các biến môi trường `DB_*` hoặc `DATABASE_URL` đã được thiết lập chưa.
- Web Service và PostgreSQL trên Render có cùng region không.

---

## 🔒 Bảo mật

- Không commit mật khẩu, secret key hoặc thông tin xác thực database.
- Không công khai thông tin đăng nhập tài khoản quản trị hoặc tài khoản demo.
- Sử dụng `DEBUG=False` trên Production.
- Thiết lập `ALLOWED_HOSTS` đúng với domain triển khai.
- Chỉ lưu các thông tin bí mật trong Environment Variables hoặc hệ thống quản lý secrets phù hợp.
