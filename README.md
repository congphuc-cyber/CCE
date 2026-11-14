# Hệ thống Quản lý Đào tạo, Thi và Cấp chứng chỉ (CCE)
> **Trung tâm Kỹ thuật Điện toán — Trường Đại học Bách Khoa, ĐHQG-HCM**  
> Giao diện chuẩn mực, hiện đại trên nền tảng Django 5.2 & django-unfold (Concept 3 "Chính Xác").

---

## ⚡ Chạy dự án lần đầu

Làm lần lượt các bước dưới đây để cài đặt, tạo database, nạp dữ liệu mẫu và đăng nhập vào hệ thống.

### Windows (PowerShell)

**Chuẩn bị:** cài Git và Python 3.10 trở lên (khuyến nghị Python 3.12). Khi cài Python, chọn **Add Python to PATH**. Mở PowerShell và kiểm tra:

```powershell
git --version
python --version
```

**1. Tải mã nguồn về máy:**

```powershell
git clone https://github.com/congphuc-cyber/LMS-.git
cd LMS-
```

**2. Cài thư viện, tạo database và nạp dữ liệu dùng thử:**

```powershell
.\scripts\setup.ps1 -Demo
```

Chờ lệnh chạy xong. Script tự tạo môi trường Python `.venv`, cài các thư viện, tạo database, áp dụng migrations, tạo tài khoản quản trị và nạp dữ liệu mẫu.

**3. Khởi động ứng dụng:**

```powershell
.\run.ps1
```

Mở **http://127.0.0.1:8001/** trên trình duyệt. Đăng nhập quản trị bằng username `cce_admin`; thông tin mật khẩu quản trị và tài khoản demo được in trong PowerShell sau bước cài đặt. Hãy lưu lại thông tin đó ở nơi an toàn. Nhấn `Ctrl+C` trong PowerShell để dừng ứng dụng.

Nếu PowerShell báo không cho phép chạy script, chạy lệnh sau trong cùng cửa sổ rồi thực hiện lại bước 2:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Linux / macOS

Đảm bảo đã cài Git và Python 3.10 trở lên. Trên Ubuntu/Debian, nếu chưa có Python hoặc hỗ trợ `venv`, cài bằng `sudo apt install git python3 python3-venv python3-pip`. Sau đó chạy trong Terminal:

```bash
git clone https://github.com/congphuc-cyber/LMS-.git
cd LMS-
chmod +x scripts/setup.sh scripts/run.sh
./scripts/setup.sh --demo
./scripts/run.sh
```

Mở **http://127.0.0.1:8001/**. Thông tin tài khoản được in trong Terminal.

> Muốn chỉ tạo database và tài khoản quản trị mà không nạp dữ liệu mẫu, bỏ `-Demo` trên Windows hoặc `--demo` trên Linux/macOS.

### Chạy bằng Docker (tùy chọn)

Nếu đã cài Docker, chạy:

```powershell
git clone https://github.com/congphuc-cyber/LMS-.git
cd LMS-
docker compose up --build
```

Compose tự khởi tạo database và dữ liệu demo. Mở **http://127.0.0.1:8001/**; thông tin tài khoản được in trong log khi container khởi động. Nhấn `Ctrl+C` để dừng.

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
