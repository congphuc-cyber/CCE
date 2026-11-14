# Hệ thống Quản lý Đào tạo, Thi và Cấp chứng chỉ (CCE)
> **Trung tâm Kỹ thuật Điện toán — Trường Đại học Bách Khoa, ĐHQG-HCM**  
> Giao diện chuẩn mực, hiện đại trên nền tảng Django 5.2 & django-unfold (Concept 3 "Chính Xác").

---

## ⚡ Cài đặt và chạy dự án

### Yêu cầu trước khi cài đặt

- Git.
- Python `>= 3.10`; khuyến nghị Python 3.12.
- Kết nối Internet trong lần cài đầu tiên để tải các thư viện Python.
- Nếu chạy bằng Docker: Docker Desktop (Windows/macOS) hoặc Docker Engine và Docker Compose plugin (Linux).

Kiểm tra Python và Git đã có trên `PATH`:

```bash
python --version
git --version
```

Trên Linux/macOS, dùng `python3 --version` nếu lệnh `python` không có.

### Cách 1: Chạy trực tiếp trên máy

#### Windows (PowerShell)

Mở PowerShell, chạy lần lượt:

```powershell
git clone https://github.com/congphuc-cyber/LMS-.git
cd LMS-
.\scripts\setup.ps1 -Demo
.\scripts\run.ps1
```

Nếu PowerShell chặn chạy script, chỉ mở quyền cho cửa sổ hiện tại rồi chạy lại lệnh cài đặt:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup.ps1 -Demo
```

#### Linux / macOS

```bash
git clone https://github.com/congphuc-cyber/LMS-.git
cd LMS-
chmod +x scripts/setup.sh scripts/run.sh
./scripts/setup.sh --demo
./scripts/run.sh
```

Trên Ubuntu/Debian, nếu máy chưa có Python hoặc hỗ trợ `venv`, có thể cài trước bằng:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

Lệnh `setup` tạo môi trường ảo `.venv`, cài thư viện từ `requirements/base.txt`, tạo `.env` nếu chưa có, chạy migration, tạo các nhóm quyền và tài khoản quản trị. Tham số `-Demo` / `--demo` nạp thêm dữ liệu mẫu để thử các màn hình nghiệp vụ. Bỏ tham số này nếu chỉ muốn khởi tạo hệ thống trống.

Sau khi khởi tạo, terminal hiển thị thông tin tài khoản quản trị; nếu có nạp dữ liệu demo, mật khẩu demo được tạo ngẫu nhiên cũng hiển thị tại đó. Lưu thông tin này ở nơi an toàn và không chia sẻ log terminal.

Lệnh `run` khởi động máy chủ phát triển trên `127.0.0.1:8001`. Giữ cửa sổ terminal đang chạy; nhấn `Ctrl+C` để dừng. Có thể chọn cổng khác, ví dụ:

```powershell
.\scripts\run.ps1 8002
```

```bash
./scripts/run.sh 8002
```

#### Tự cấu hình tài khoản quản trị khi cài local

Muốn chọn tên đăng nhập hoặc mật khẩu quản trị trước lần khởi tạo đầu tiên, đặt biến môi trường trong chính cửa sổ terminal rồi chạy `setup`. Ví dụ PowerShell:

```powershell
$env:BOOTSTRAP_SUPERUSER_USERNAME = "cce_admin"
$env:BOOTSTRAP_SUPERUSER_PASSWORD = "thay-bang-mat-khau-rieng"
.\scripts\setup.ps1 -Demo
```

Trên Linux/macOS:

```bash
export BOOTSTRAP_SUPERUSER_USERNAME="cce_admin"
export BOOTSTRAP_SUPERUSER_PASSWORD="thay-bang-mat-khau-rieng"
./scripts/setup.sh --demo
```

File `.env` được tạo làm mẫu cấu hình cục bộ và bị Git bỏ qua. Docker Compose tự đọc file này; ứng dụng chạy local không tự nạp `.env`, vì vậy cần đặt biến trong terminal như ví dụ trên nếu muốn thay đổi cấu hình lúc bootstrap. Không commit mật khẩu hoặc khóa bí mật lên Git. Để đổi mật khẩu quản trị sau khi đã cài đặt, chạy từ thư mục gốc:

```powershell
.\.venv\Scripts\python.exe .\site1\manage.py changepassword cce_admin
```

Trên Linux/macOS:

```bash
./.venv/bin/python site1/manage.py changepassword cce_admin
```

Lưu ý: chạy lại `bootstrap` sẽ đặt lại mật khẩu tài khoản quản trị theo biến môi trường hiện tại hoặc giá trị mặc định của ứng dụng.

### Cách 2: Chạy bằng Docker Compose

Cài và mở Docker trước, sau đó clone dự án:

```bash
git clone https://github.com/congphuc-cyber/LMS-.git
cd LMS-
```

Nếu muốn cấu hình tài khoản hoặc biến môi trường, sao chép `.env.example` thành `.env` và sửa giá trị cục bộ **trước khi** chạy Compose. Có thể bỏ qua bước này để chạy bằng cấu hình mặc định. Không commit `.env`:

```powershell
Copy-Item .env.example .env
```

```bash
cp .env.example .env
```

Khởi động dịch vụ tại thư mục gốc:

```bash
docker compose up --build
```

Compose sẽ build image, áp dụng migration, khởi tạo nhóm quyền và nạp dữ liệu demo theo cấu hình mặc định. Cơ sở dữ liệu và media được lưu trong Docker named volumes nên vẫn còn sau khi container dừng. Mở `http://127.0.0.1:8001/`; dùng `Ctrl+C` để dừng. Để chạy nền, dùng `docker compose up --build -d`; dừng và gỡ container bằng `docker compose down` (lệnh này giữ dữ liệu trong volumes).

---

## 🌐 Địa chỉ truy cập & Tài khoản mặc định

Máy chủ mặc định lắng nghe tại: `http://127.0.0.1:8001/`

| Khu vực | Đường dẫn URL | Mô tả |
|---------|---------------|-------|
| **Trang chủ** | `http://127.0.0.1:8001/` | Cổng thông tin CCE dành cho công chúng & thí sinh |
| **Đăng nhập** | `http://127.0.0.1:8001/login/` | Cổng đăng nhập phân quyền cho cán bộ, giáo viên |
| **Khu vực Quản trị** | `http://127.0.0.1:8001/admin/` | Trang quản trị hiện đại (Unfold Admin) |
| **Bảng điều khiển** | `http://127.0.0.1:8001/dashboard/` | Chỉ số hoạt động, biểu đồ dành cho Giáo vụ |
| **Kiểm tra giao diện** | `http://127.0.0.1:8001/ui-demo/` | Showcase toàn bộ thành phần Design Tokens (khi DEBUG=True) |

### Danh sách tài khoản kiểm thử mặc định:
> Chạy `python manage.py seed_demo_data` trong môi trường DEBUG (hoặc `python manage.py bootstrap --demo`) để tạo các tài khoản vai trò và dữ liệu mẫu. Đặt `DEMO_TEST_PASSWORD` trước khi chạy để chọn mật khẩu; nếu không đặt, lệnh tạo mật khẩu ngẫu nhiên và in ra một lần. Lệnh không đổi mật khẩu của tài khoản đã tồn tại. Tài khoản quản trị dùng `BOOTSTRAP_SUPERUSER_PASSWORD`.

| Vai trò | Tên đăng nhập (Username) | Quyền hạn chính |
|---------|-------------------------|-----------------|
| **Quản trị viên (Superuser)** | `cce_admin` | Toàn quyền hệ thống, phân quyền vai trò, tài khoản, nhật ký thao tác |
| **Giáo vụ** | `cce_giaovu_test_1461` | Quản lý khóa học, lớp học, học viên, phân công giáo viên, đóng lớp |
| **Giáo viên** | `cce_giaovien_test_65df` | Xem danh sách và chi tiết các lớp được phân công giảng dạy |
| **Cán bộ thi** | `cce_canbothi_test_0656` | Quản trị đợt thi, phòng thi, điểm thi và chứng chỉ |

---

## 🛠 Yêu cầu hệ thống

- **Hệ điều hành:** Windows 10/11, macOS, hoặc Linux (Ubuntu, Debian, Fedora, CentOS)
- **Python:** Phiên bản `>= 3.10` (Khuyến nghị **Python 3.12**)
- **Trình duyệt:** Chrome, Edge, Firefox, Safari (hỗ trợ đầy đủ CSS Variables và Flexbox/Grid)
- *(Tùy chọn)* **Docker & Docker Compose** nếu chạy bằng container.

---

## 📁 Cấu trúc thư mục dự án

```
lmssss/
├── .env.example              # Mẫu các biến môi trường
├── .gitignore                # Danh sách loại trừ khi commit Git
├── Dockerfile                # Cấu hình container Python 3.12
├── docker-compose.yml        # Docker compose khởi chạy dịch vụ
├── Makefile                  # Phím tắt thao tác cho Linux/macOS
├── requirements/
│   ├── base.txt              # Thư viện lõi (Django 5.2, django-unfold 0.110.1)
│   ├── dev.txt               # Thư viện cho môi trường phát triển
│   └── prod.txt              # Thư viện cho môi trường sản xuất (gunicorn)
├── scripts/
│   ├── setup.ps1             # Cài đặt tự động trên Windows
│   ├── run.ps1               # Khởi chạy trên Windows
│   ├── setup.sh              # Cài đặt tự động trên Linux/macOS
│   ├── run.sh                # Khởi chạy trên Linux/macOS
│   └── entrypoint.sh         # Điểm khởi chạy trong Docker
└── site1/                    # Thư mục mã nguồn Django
    ├── manage.py             # Script quản trị Django
    ├── site1/                # Cấu hình dự án cấp cao
    │   ├── urls.py           # Định tuyến URL chính
    │   ├── wsgi.py / asgi.py # Cổng kết nối WSGI/ASGI
    │   └── settings/         # Cấu hình môi trường đã tách biệt
    │       ├── __init__.py   # Tự động nạp cấu hình theo DJANGO_ENV
    │       ├── base.py       # Cấu hình dùng chung & unfold settings
    │       ├── dev.py        # Cấu hình phát triển (DEBUG=True, SQLite)
    │       └── prod.py       # Cấu hình sản xuất bảo mật (DEBUG=False)
    └── training/             # App nghiệp vụ quản lý đào tạo & khảo thí
        ├── models.py         # 8 Models nghiệp vụ (khóa học, lớp học, học viên,...)
        ├── views.py          # View xử lý logic & quyền hạn theo vai trò
        ├── forms.py          # Form xác thực, ghi danh, phân công
        ├── admin.py          # Cấu hình Unfold ModelAdmin chuyên sâu
        ├── access.py         # Kiểm tra phân quyền chức năng
        ├── audit.py          # Ghi nhận nhật ký thao tác & snapshot
        ├── navigation.py     # Cấu hình menu sidebar theo vai trò
        ├── context_processors.py
        ├── templatetags/     # Filter nhãn trạng thái và helper
        ├── management/
        │   └── commands/
        │       ├── bootstrap.py       # Lệnh khởi tạo toàn diện hệ thống
        │       ├── create_cce_roles.py # Tạo 4 nhóm vai trò
        │       └── seed_demo_data.py   # Nạp dữ liệu mẫu
        ├── templates/training/        # Giao diện viết mới hoàn toàn theo Concept 3
        └── static/training/css/       # Design Tokens & CSS không dùng class cũ
            ├── tokens.css             # Biến CSS (màu, font, spacing, radius)
            ├── cce-core.css           # Thành phần UI dùng chung
            └── cce-unfold.css         # Tùy biến Unfold Admin đồng bộ tokens
```

---

## ⚙️ Danh sách biến môi trường

| Tên biến | Mặc định (Dev) | Mô tả |
|----------|----------------|-------|
| `DJANGO_SECRET_KEY` | *(Khóa dùng cho phát triển local)* | Khóa bí mật Django. **Bắt buộc đặt giá trị bí mật trên Production**. |
| `DJANGO_ENV` | `development` | Môi trường chạy (`development` hoặc `production`). |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` | Danh sách hostname được phép truy cập (cách nhau bởi dấu phẩy). |
| `PORT` | `8001` | Cổng mạng máy chủ web lắng nghe. |
| `BOOTSTRAP_SUPERUSER_USERNAME` | `cce_admin` | Tên tài khoản Superuser tự tạo khi chạy bootstrap. |
| `BOOTSTRAP_SUPERUSER_PASSWORD` | *(cấu hình riêng ở môi trường local)* | Mật khẩu cho Superuser. Không dùng hoặc commit mật khẩu thật trong repository. |
| `BOOTSTRAP_SUPERUSER_EMAIL` | `admin@cce.edu.vn` | Email liên hệ cho Superuser. |
| `DEMO_TEST_PASSWORD` | *(tạo ngẫu nhiên nếu bỏ trống)* | Mật khẩu cho các tài khoản vai trò mẫu được tạo mới bằng `seed_demo_data`. |

---

## 🧪 Cách chạy kiểm thử (Tests)

Hệ thống đi kèm bộ kiểm thử tự động 35 test cases kiểm tra toàn diện phân quyền, điều hướng, logic lớp học, nhật ký thao tác và xác thực:

```bash
# Chạy toàn bộ test
cd site1
python manage.py test training
```

---

## 📦 Lệnh tạo dữ liệu mẫu (Demo Data)

Để nạp lại dữ liệu mẫu (8 khóa học, 24 lớp học, 12 giáo viên, 120 học viên, phân công và nhật ký):

```bash
cd site1
python manage.py bootstrap --demo
```

Để tạo dữ liệu kiểm thử cho các luồng kết quả cuối khóa, chứng nhận/chứng chỉ,
lịch sử chuyển lớp và tra cứu công khai, chạy lệnh sau trong môi trường
`DEBUG=True`. Dữ liệu dùng tên tự nhiên và email thuộc miền dành riêng
`example.test`; đây là dữ liệu tổng hợp, không phải hồ sơ thật. Lệnh có thể
chạy lặp lại và không xóa dữ liệu đang có:

```bash
python manage.py seed_workflow_test_data
```

Thông tin tra cứu được in sau khi chạy lệnh. Email `.test` không nhận thư thật
và chỉ dành cho môi trường phát triển.

Lệnh hoàn toàn idempotent (chạy nhiều lần không gây lỗi hay trùng lặp).

---

## 🔒 Cấu hình cho môi trường Sản xuất (Production)

Khi đưa lên môi trường máy chủ sản xuất:

1. Đặt biến môi trường:
   ```bash
   export DJANGO_ENV=production
   export DJANGO_SECRET_KEY="khoa-bi-mat-dai-va-phuc-tap-cua-ban"
   export ALLOWED_HOSTS="cce.edu.vn,www.cce.edu.vn"
   ```
2. Thu thập file tĩnh:
   ```bash
   cd site1
   python manage.py collectstatic --noinput
   ```
3. Khởi chạy với Gunicorn:
   ```bash
   gunicorn site1.wsgi:application --bind 0.0.0.0:8001 --workers 4
   ```

---

## ❓ Xử lý sự cố thường gặp (Troubleshooting)

1. **Lỗi: `No module named unfold`**  
   *Giải pháp:* Đảm bảo đã kích hoạt môi trường ảo `.venv` hoặc chạy `pip install -r requirements/base.txt`.

2. **Lỗi: Cổng 8001 bị chiếm dụng (`Address already in use`)**  
   *Giải pháp:* Chạy trên cổng khác, ví dụ:  
   `.\scripts\run.ps1 8002` hoặc `./scripts/run.sh 8002`

3. **Thay đổi mật khẩu tài khoản Quản trị:**  
   *Giải pháp:* Chạy lệnh: `python manage.py changepassword cce_admin`
