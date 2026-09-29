# 開發環境設定

## Python 版本

- Python 3.11.9

## 虛擬環境 (venv)

專案已建立虛擬環境於 `venv/` 目錄，所有 Python 套件都安裝在此虛擬環境內，不會影響系統 Python。

### Windows PowerShell

啟動 (activate):

```powershell
.\venv\Scripts\Activate.ps1
```

若出現「無法載入，因為執行原則」的錯誤，需先允許本機腳本執行（僅需設定一次）：

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

關閉虛擬環境 (deactivate)：

```powershell
deactivate
```

### Windows cmd.exe

啟動：

```cmd
venv\Scripts\activate.bat
```

關閉：

```cmd
deactivate
```

### Git Bash / WSL

啟動：

```bash
source venv/Scripts/activate
```

關閉：

```bash
deactivate
```

## 安裝套件

虛擬環境啟動後，安裝所有相依套件：

```bash
pip install -r requirements.txt
```

## 已安裝套件 (requirements.txt)

- Flask
- Flask-CORS
- Flask-SQLAlchemy
- Flask-Migrate
- python-dotenv

## 目前狀態

- [x] Python venv 建立完成
- [x] requirements.txt 建立完成，套件已安裝於 venv 內
- [ ] Flask 應用程式尚未建立（尚未開始撰寫業務功能）
- [ ] Frontend (Vue 3 + Node.js) 尚未建立
