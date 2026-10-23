import importlib.util
import os
import ssl
import sys
import traceback
import urllib.request
from datetime import datetime
import flet as ft

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/DemonSwans/MasterADHD/master"


def get_ssl_context():
    """Tworzy kontekst SSL z bezpiecznym fallbackiem dla środowisk mobilnych (Android)."""
    try:
        return ssl._create_unverified_context()
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return None


def get_ota_dir():
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.expanduser("~")
    ota_dir = os.path.join(base, ".master_adhd_data")
    try:
        os.makedirs(ota_dir, exist_ok=True)
        return ota_dir
    except Exception:
        return "."

OTA_DIR = get_ota_dir()
OTA_LOGIC_FILE = os.path.join(OTA_DIR, "app_logic_ota.py")
OTA_VERSION_FILE = os.path.join(OTA_DIR, "ota_version.txt")


def is_newer_version(remote_ver: str, local_ver: str) -> bool:
    try:
        r_parts = [int(x) for x in remote_ver.strip().split(".")]
        l_parts = [int(x) for x in local_ver.strip().split(".")]
        return r_parts > l_parts
    except Exception:
        return remote_ver.strip() != local_ver.strip()


def get_installed_ota_version() -> str:
    """Sprawdza wersję zgodnie z priorytetem z app_logic.get_local_version()."""
    try:
        import app_logic
        # Używamy get_local_version() z repozytorium (priorytet) zamiast starego FALLBACK_VERSION
        return getattr(app_logic, "APP_VERSION", "0.0.0")
    except Exception as e:
        print(f"[OTA] Błąd pobierania wersji z app_logic: {e}")
        # Fallback do ota_version.txt (tylko jeśli nie ma kodu z repozytorium)
        if os.path.exists(OTA_VERSION_FILE):
            try:
                with open(OTA_VERSION_FILE, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
        return "0.0.0"


def check_and_download_ota(force=False, status_cb=None) -> tuple[bool, str]:
    """Sprawdza na GitHubie czy jest nowszy kod i pobiera go bezpośrednio do pamięci urządzenia."""
    ts = int(datetime.now().timestamp())
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "User-Agent": "MasterADHD-OTA/1.0"
    }
    ctx = get_ssl_context()

    def update_status(text, progress=None):
        if status_cb:
            try:
                status_cb(text, progress)
            except Exception:
                pass

    try:
        update_status("Sprawdzanie dostępności aktualizacji...", 0.2)
        url_ver = f"{GITHUB_RAW_BASE}/version.txt?t={ts}"
        req_ver = urllib.request.Request(url_ver, headers=headers)
        with urllib.request.urlopen(req_ver, timeout=5, context=ctx) as resp:
            remote_ver = resp.read().decode("utf-8").strip()

        local_ver = get_installed_ota_version()
        print(f"[OTA] Sprawdzanie aktualizacji: zdalna v{remote_ver}, lokalna v{local_ver}")

        if force or is_newer_version(remote_ver, local_ver):
            print(f"[OTA] Wykryto nowszą wersję lub wymuszenie (v{remote_ver}). Pobieranie kodu...")
            update_status(f"Pobieranie nowego kodu v{remote_ver}...", 0.5)

            url_code = f"{GITHUB_RAW_BASE}/app_logic.py?t={ts}"
            req_code = urllib.request.Request(url_code, headers=headers)
            with urllib.request.urlopen(req_code, timeout=8, context=ctx) as resp_code:
                new_code = resp_code.read().decode("utf-8")

            update_status("Weryfikacja i instalacja aktualizacji...", 0.8)
            # Weryfikacja czy pobrany kod to poprawny Python (ochrona przed uciętym plikiem)
            compile(new_code, "app_logic_ota.py", "exec")
            with open(OTA_LOGIC_FILE, "w", encoding="utf-8") as f:
                f.write(new_code)
            with open(OTA_VERSION_FILE, "w", encoding="utf-8") as f:
                f.write(remote_ver)

            print(f"[OTA] Pomyślnie pobrano i zapisano nową wersję v{remote_ver}.")
            update_status(f"Zaktualizowano pomyślnie do v{remote_ver}!", 1.0)
            return True, remote_ver
        else:
            print(f"[OTA] Aplikacja jest aktualna (v{local_ver}).")
            update_status(f"Aplikacja jest aktualna (v{local_ver})", 1.0)
    except Exception as e:
        print(f"[OTA] Błąd podczas sprawdzania lub pobierania aktualizacji: {e}")
        traceback.print_exc()
        update_status(f"Błąd sieci OTA: {str(e)[:40]}", 1.0)
    return False, get_installed_ota_version()


def load_and_run(page: ft.Page):
    disable_ota = os.environ.get("DISABLE_OTA", "0") == "1"
    updated = False
    new_ver = ""

    # Wizualny ekran ładowania / paska postępu aktualizacji
    if not disable_ota and not hasattr(page, "_ota_checked"):
        page._ota_checked = True
        page.bgcolor = "#0d1117"
        page.theme_mode = ft.ThemeMode.DARK

        progress_bar = ft.ProgressBar(width=260, color="#f59e0b", bgcolor="#21262d", value=0.1)
        status_label = ft.Text("Inicjalizacja aplikacji...", size=12, color="#8b949e")
        ver_label = ft.Text(f"Wersja lokalna: v{get_installed_ota_version()}", size=11, color="#484f58")

        splash = ft.Container(
            expand=True,
            alignment=ft.Alignment(0, 0),
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.BOLT, size=40, color="#f59e0b"),
                    ft.Text("Master ADHD", size=20, weight=ft.FontWeight.BOLD, color="#f0f6fc"),
                    ft.Container(height=14),
                    progress_bar,
                    ft.Container(height=6),
                    status_label,
                    ft.Container(height=4),
                    ver_label,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                tight=True
            )
        )

        page.controls.clear()
        page.add(ft.SafeArea(splash))
        page.update()

        def status_cb(msg, progress):
            status_label.value = msg
            if progress is not None:
                progress_bar.value = progress
            page.update()

        updated, new_ver = check_and_download_ota(status_cb=status_cb)

    try:
        # 2. Jeśli mamy pobrany nowszy moduł w pamięci podręcznej OTA -> ładujemy go od razu!
        page.controls.clear()
        
        # PRIORITY: OTA cache > Folder data > Wbudowany kod
        target_ota = None
        
        # Spróbuj najpierw z repozytorium (aktualny kod z GitHub)
        if updated and os.path.exists(OTA_LOGIC_FILE):
            print(f"[OTA] Priorytet 1: Ładowanie z OTA cache {OTA_LOGIC_FILE}")
            target_ota = OTA_LOGIC_FILE
        
        # Fallback 1: Sprawdź folder data w App Data (Android)
        if not target_ota and base_env:
            alt_ota = os.path.join(base_env, "data", "app_logic_ota.py")
            if os.path.exists(alt_ota):
                print(f"[OTA] Priorytet 2: Ładowanie z Alt OTA {alt_ota}")
                target_ota = alt_ota
        
        # Fallback 2: Sprawdź główny folder OTA (starsze wersje main.py)
        if not target_ota and os.path.exists(OTA_LOGIC_FILE):
            print(f"[OTA] Priorytet 3: Ładowanie z OTA cache {OTA_LOGIC_FILE}")
            target_ota = OTA_LOGIC_FILE
        
        # Fallback 3: Sprawdź wersję w folderze projektu (repozytorium lokalne)
        repo_logic = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_logic.py")
        if not target_ota and os.path.exists(repo_logic):
            print(f"[OTA] Priorytet 4: Ładowanie z repozytorium {repo_logic}")
            target_ota = repo_logic
        
        # Fallback 4: Sprawdź wbudowany kod (ostatnia deska ratunku)
        if not target_ota:
            print("[OTA] Priorytet 5: Ładowanie WBUDOWANEGO app_logic.py (Najgorsze)")
            try:
                import app_logic
                # Wydruk wersji z wbudowanego modułu dla debugowania
                print(f"[OTA DEBUG] Wbudowany APP_VERSION = {getattr(app_logic, 'APP_VERSION', 'UNKNOWN')}")
                target_ota = None  # Pozwól na import przez sys.modules
            except Exception as e:
                print(f"[OTA] Błąd przy ładowaniu wbudowanego: {e}")
        
        # Jeśli kod został pobrany (updated=True) -> od razu ładujemy nowy kod bez sprawdzania pliku wersji!
        if updated and target_ota:
            print(f"[OTA] ŁADOWANIE NOWEJ WERSJI v{new_ver} z {target_ota}")
            spec = importlib.util.spec_from_file_location("app_logic_ota", target_ota)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["app_logic"] = mod
            spec.loader.exec_module(mod)
            mod.main(page)
        elif updated and not target_ota:
            # updated=True ale brak pliku - spróbuj ponownie z repozytorium
            print(f"[OTA] updated=True ale brak OTA pliku! Spróbuję wbudowanego...")
            import app_logic
            app_logic.main(page)
        elif target_ota:
            print(f"[OTA] Ładowanie pobranego modułu z {target_ota}")
            spec = importlib.util.spec_from_file_location("app_logic_ota", target_ota)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["app_logic"] = mod
            spec.loader.exec_module(mod)
            mod.main(page)
        else:
            # 3. Wersja bazowa wbudowana w projekt (tylko jak brakuje OTA plików!)
            print("[OTA] ⚠️ Brak OTA plików - ładuję WBUDOWANY app_logic.py")
            import app_logic
            app_logic.main(page)

        if updated:
            snack = ft.SnackBar(
                ft.Text(f"🚀 Zaktualizowano w locie do wersji v{new_ver}!"),
                open=True,
                bgcolor=ft.Colors.GREEN_800
            )
            page.overlay.append(snack)
            if hasattr(page, "open"):
                try:
                    page.open(snack)
                except Exception:
                    pass
            elif hasattr(page, "snack_bar"):
                try:
                    page.snack_bar = snack
                    page.snack_bar.open = True
                except Exception:
                    pass
            page.update()

    except Exception as e:
        # 4. ZABEZPIECZENIE: Jeśli pobrany plik ma błąd, nie blokujemy aplikacji!
        err_msg = traceback.format_exc()
        print(f"[OTA Error] Błąd podczas uruchamiania modułu: {err_msg}")
        if os.path.exists(OTA_LOGIC_FILE):
            try:
                os.remove(OTA_LOGIC_FILE)
            except Exception:
                pass

        def retry_ota( _ ):
            page.controls.clear()
            check_and_download_ota(force=True)
            load_and_run(page)

        page.controls.clear()
        page.add(
            ft.SafeArea(
                ft.Container(
                    padding=20,
                    content=ft.Column([
                        ft.Text("⚠️ Błąd w module aplikacji (Przywrócono wersję bazową)", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_400),
                        ft.Text(err_msg, size=11, selectable=True),
                        ft.Button("🔄 Pobierz poprawkę z GitHuba", bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE, on_click=retry_ota)
                    ], scroll=ft.ScrollMode.AUTO)
                )
            )
        )
        page.update()


def main(page: ft.Page):
    load_and_run(page)


# Eksport funkcji z app_logic dla pełnej kompatybilności z test_app.py
from app_logic import *  # noqa: E402, F403

if __name__ == "__main__":
    ft.run(main)