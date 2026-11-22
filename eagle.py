import ipaddress
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from urllib.parse import urlencode

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSpinBox, QTabWidget,
    QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu, QPushButton,
    QStackedWidget, QStyle, QVBoxLayout, QWidget,
)

IDLE_SECONDS = 300
APP_VERSION = "0.2.1"
RELEASE_REPO = "lucasmsilvaltda-debug/EAGLE"
UPDATE_INTERVAL_MS = 5 * 60 * 1000
Lifecycle = QWebEnginePage.LifecycleState
SEARCH_ENGINES = {
    "DuckDuckGo": ("https://duckduckgo.com/", "q"),
    "Google": ("https://www.google.com/search", "q"),
    "Bing": ("https://www.bing.com/search", "q"),
    "Brave Search": ("https://search.brave.com/search", "q"),
}
DEFAULTS = {
    "search": "DuckDuckGo", "startup": "", "home": "", "zoom": 100,
    "collapse": True, "third_party": True, "javascript": True, "images": True,
    "permissions": False, "ask_download": True, "download_folder": "",
    "memory_saver": True, "idle_minutes": IDLE_SECONDS // 60,
    "update_checks": True,
}

STYLE = """
QMainWindow, QWidget#shell { background: #0a1426; color: #f7f9ff; }
QWidget#shell { font-family: 'Segoe UI'; font-size: 14px; }
QLabel { color: #f7f9ff; }
QLabel#wordmark { font-size: 25px; font-weight: 800; color: #ffffff; }
QLabel#muted { color: #b9c8e2; font-size: 12px; }
QLabel#headline { color: #ffffff; font-size: 48px; font-weight: 700; }
QFrame#rail { background: #142744; border-radius: 16px; }
QFrame#surface { background: #0c192e; border-radius: 16px; }
QLineEdit { background: #0b1930; color: #f7f9ff; border: 1px solid #5273a2;
            border-radius: 9px; padding: 10px; selection-background-color: #31538a; }
QLineEdit:focus { border-color: #ff7080; }
QPushButton { background: #243e65; color: #f7faff; border: 1px solid transparent;
              border-radius: 8px; padding: 7px 10px; }
QPushButton:hover { background: #2f507e; }
QPushButton:focus { border-color: #ff7080; }
QPushButton:disabled { color: #a2b2cc; background: #1c3151; }
QPushButton#primary { background: #ae2238; color: #ffffff; font-weight: 700; }
QPushButton#primary:hover { background: #c52d43; }
QListWidget { background: transparent; color: #edf3ff; border: none; outline: none; }
QListWidget::item { padding: 9px; margin: 2px 0; border-radius: 7px; }
QListWidget::item:hover { background: #27446b; }
QListWidget::item:selected { background: #2e5082; color: #fff; }
QListWidget#compactTabs::item { padding: 0; margin: 0; }
QMenu { background: #172d4e; color: #f7faff; padding: 5px; }
QMenu::item { padding: 6px 20px; }
QMenu::item:selected { background: #2a4770; }
QDialog#preferences, QTabWidget::pane { background: #142744; border: 0; }
QTabBar::tab { background: #243e65; color: #f7faff; padding: 10px; }
QTabBar::tab:selected { background: #2e5082; }
QCheckBox, QComboBox, QSpinBox { color: #f7faff; }
QComboBox, QComboBox QAbstractItemView, QSpinBox { background: #0b1930; color: #f7faff; }
"""

def address_url(text, search_engine="DuckDuckGo"):
    text = text.strip()
    if not text:
        return QUrl()
    explicit_url = text.lower().startswith(("https://", "http://"))
    authority = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = authority.rsplit(":", 1)[0] if authority.count(":") == 1 else authority
    try:
        is_ip = bool(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        is_ip = False
    looks_like_host = ("." in host or host.lower() == "localhost" or is_ip)
    if explicit_url or (looks_like_host and not any(c.isspace() for c in authority)):
        url = QUrl(text if explicit_url else "https://" + text)
        if url.isValid() and url.host() and not url.userInfo():
            return url
    endpoint, key = SEARCH_ENGINES.get(search_engine, SEARCH_ENGINES["DuckDuckGo"])
    return QUrl(endpoint + "?" + urlencode({key: text}))


def page_url(text):
    url = QUrl.fromUserInput(text.strip())
    if url.isValid() and url.scheme() in ("https", "http") and url.host() and not url.userInfo():
        return url
    return QUrl()


def release_version(tag):
    match = re.fullmatch(r"v?(\d{1,5})\.(\d{1,5})\.(\d{1,5})", tag) if isinstance(tag, str) else None
    return tuple(map(int, match.groups())) if match else None


def available_release(payload):
    if not isinstance(payload, dict) or payload.get("draft") is not False or payload.get("prerelease") is not False:
        return None
    tag = payload.get("tag_name")
    version = release_version(tag)
    if version is None or version <= release_version(APP_VERSION):
        return None
    label = ".".join(map(str, version))
    assets = payload.get("assets")
    if not isinstance(assets, list) or not any(
        isinstance(asset, dict)
        and asset.get("name") == f"EAGLE-{label}-Instalador-x64.exe"
        and asset.get("state") == "uploaded"
        for asset in assets
    ):
        return None
    return label, QUrl(f"https://github.com/{RELEASE_REPO}/releases/tag/{tag}")


class PreferencesDialog(QDialog):
    def __init__(self, browser):
        super().__init__(browser, objectName="preferences")
        self.browser = browser
        self.fields = {}
        self.setWindowTitle("Configurações do EAGLE")
        self.resize(670, 390)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        navigation = self.section(tabs, "Navegação")
        search = QComboBox()
        search.addItems(SEARCH_ENGINES)
        self.add(navigation, "Buscador padrão", "search", search)
        self.add(navigation, "Ao iniciar", "startup", QLineEdit(placeholderText="Nova aba, se vazio"))
        self.add(navigation, "Página inicial (Alt+Home)", "home", QLineEdit(placeholderText="Nova aba, se vazio"))

        appearance = self.section(tabs, "Aparência")
        zoom = QSpinBox(minimum=25, maximum=500, singleStep=10, suffix="%")
        self.add(appearance, "Zoom padrão das páginas", "zoom", zoom)
        self.add(appearance, "Barra lateral", "collapse", QCheckBox("Recolher ao navegar"))

        privacy = self.section(tabs, "Privacidade")
        self.add(privacy, "Cookies de terceiros", "third_party", QCheckBox("Bloquear"))
        self.add(privacy, "JavaScript", "javascript", QCheckBox("Permitir"))
        self.add(privacy, "Imagens", "images", QCheckBox("Mostrar"))
        self.add(privacy, "Câmera, microfone, localização\ne notificações", "permissions",
                 QCheckBox("Perguntar quando um site solicitar"))
        privacy.addRow(QLabel("Cookies e cache são mantidos em memória durante a sessão."))
        privacy.addRow(browser.button("Limpar cookies e cache", "Limpar cookies e cache", self.clear_data))

        downloads = self.section(tabs, "Downloads")
        folder = QWidget()
        row = QHBoxLayout(folder)
        row.setContentsMargins(0, 0, 0, 0)
        path = QLineEdit(placeholderText="Pasta Downloads do Windows")
        path.setAccessibleName("Pasta de downloads")
        row.addWidget(path, 1)
        row.addWidget(browser.button("Escolher…", "Escolher pasta", self.choose_folder))
        self.fields["download_folder"] = path
        downloads.addRow("Local padrão", folder)
        self.add(downloads, "Antes de salvar", "ask_download", QCheckBox("Perguntar onde salvar"))

        performance = self.section(tabs, "Desempenho")
        self.add(performance, "Economia de memória", "memory_saver", QCheckBox("Recolher abas inativas"))
        self.add(performance, "Tempo de inatividade", "idle_minutes",
                 QSpinBox(minimum=1, maximum=120, suffix=" min"))

        updates = self.section(tabs, "Atualizações")
        updates.addRow("Versão instalada", QLabel(APP_VERSION))
        self.add(updates, "Avisos de atualização", "update_checks",
                 QCheckBox("Verificar automaticamente a cada 5 minutos"))
        updates.addRow(browser.button("Verificar agora", "Verificar atualizações agora",
                                      lambda: browser.check_updates(manual=True)))

        buttons = QDialogButtonBox()
        buttons.addButton("Salvar", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        reset = buttons.addButton("Restaurar padrões", QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(lambda: self.populate(DEFAULTS))
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.populate(browser.preferences)
        self.fields["memory_saver"].toggled.connect(self.fields["idle_minutes"].setEnabled)
        self.fields["idle_minutes"].setEnabled(self.fields["memory_saver"].isChecked())

    def section(self, tabs, name):
        page = QWidget()
        tabs.addTab(page, name)
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)
        return form

    def add(self, form, label, key, widget):
        widget.setAccessibleName(label)
        self.fields[key] = widget
        form.addRow(label, widget)

    def populate(self, values):
        for key, widget in self.fields.items():
            value = values[key]
            if isinstance(widget, QCheckBox):
                widget.setChecked(value)
            elif isinstance(widget, QSpinBox):
                widget.setValue(value)
            elif isinstance(widget, QComboBox):
                widget.setCurrentText(value)
            else:
                widget.setText(value)

    def choose_folder(self):
        path = self.fields["download_folder"]
        directory = QFileDialog.getExistingDirectory(self, "Pasta de downloads", path.text())
        if directory:
            path.setText(directory)

    def clear_data(self):
        reply = QMessageBox.question(self, "Limpar cookies e cache",
                                     "Limpar cookies e cache desta sessão? Isso pode desconectar você dos sites.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.browser.profile.cookieStore().deleteAllCookies()
            self.browser.profile.clearHttpCache()

    def save(self):
        values = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                values[key] = widget.value()
            elif isinstance(widget, QComboBox):
                values[key] = widget.currentText()
            else:
                values[key] = widget.text().strip()
        for key in ("startup", "home"):
            if values[key]:
                url = page_url(values[key])
                if url.isEmpty():
                    QMessageBox.warning(self, "Endereço inválido", "Digite um endereço HTTP ou HTTPS válido.")
                    return
                values[key] = url.toString()
        self.browser.save_preferences(values)
        self.accept()


class Privacy:
    def __init__(self):
        self.exceptions = frozenset()
        self.block_third_party = True

    def allows_cookie(self, request):
        return (not self.block_third_party or not request.thirdParty
                or request.firstPartyUrl.host() in self.exceptions)

    def toggle(self, host):
        self.exceptions = self.exceptions.symmetric_difference({host})


@dataclass
class Tab:
    item: QListWidgetItem
    widget: QWidget
    view: QWebEngineView | None = None
    inactive_since: float = field(default_factory=monotonic)
    keep_active: bool = False


class Eagle(QMainWindow):
    def __init__(self, settings_store=None):
        super().__init__()
        self.setWindowTitle("EAGLE")
        self.resize(1240, 790)
        self.setMinimumSize(650, 450)
        self.setStyleSheet(STYLE)
        self.tabs = []
        self.sidebar_compact = False
        self.fullscreen_tab = None
        self.previous_window_state = Qt.WindowState.WindowNoState
        self.privacy = Privacy()
        self.settings_store = settings_store if settings_store is not None else QSettings("EAGLE", "EAGLE")
        self.preferences = {
            key: self.settings_store.value(key, default, type=type(default))
            for key, default in DEFAULTS.items()
        }
        self.update_network = QNetworkAccessManager(self)
        self.update_reply = None
        self.manual_update_check = False
        self.last_update_notice = None
        self.update_notice = None
        self.update_timer = QTimer(self, interval=UPDATE_INTERVAL_MS)
        self.update_timer.timeout.connect(self.check_updates)
        self.initial_update_timer = QTimer(self, interval=15000, singleShot=True)
        self.initial_update_timer.timeout.connect(self.check_updates)
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheMaximumSize(24 * 1024 * 1024)
        self.profile.cookieStore().setCookieFilter(self.privacy.allows_cookie)
        self.profile.downloadRequested.connect(self.download)
        attributes = QWebEngineSettings.WebAttribute
        settings = self.profile.settings()
        settings.setAttribute(attributes.DnsPrefetchEnabled, False)
        settings.setAttribute(attributes.WebRTCPublicInterfacesOnly, True)
        settings.setAttribute(attributes.PlaybackRequiresUserGesture, True)
        settings.setAttribute(attributes.FullScreenSupportEnabled, True)
        settings.setUnknownUrlSchemePolicy(
            QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes
        )

        shell = QWidget(objectName="shell")
        self.setCentralWidget(shell)
        layout = QHBoxLayout(shell)
        self.shell_layout = layout
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        rail = QFrame(objectName="rail")
        self.rail = rail
        rail.setFixedWidth(270)
        layout.addWidget(rail)
        side = QVBoxLayout(rail)
        side.setContentsMargins(16, 17, 16, 16)
        side.setSpacing(10)
        self.sidebar_toggle = self.button("«", "Recolher barra lateral", self.toggle_sidebar)
        side.addWidget(self.sidebar_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.sidebar_header = QWidget()
        header = QVBoxLayout(self.sidebar_header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        side.addWidget(self.sidebar_header)
        brand = QHBoxLayout()
        brand.setSpacing(11)
        artwork = QPixmap(str(Path(__file__).with_name("aguia.png")))
        if not artwork.isNull():
            emblem = QLabel()
            emblem.setAccessibleName("Águia-americana com a bandeira dos Estados Unidos")
            scale = emblem.devicePixelRatioF()
            pixels = round(52 * scale)
            artwork = artwork.scaled(pixels, pixels, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            artwork.setDevicePixelRatio(scale)
            emblem.setPixmap(artwork)
            brand.addWidget(emblem)
        brand.addWidget(QLabel("EAGLE", objectName="wordmark"))
        brand.addStretch()
        header.addLayout(brand)

        navigation = QHBoxLayout()
        self.back = self.button("←", "Voltar", lambda: self.navigate_action("back"))
        self.forward = self.button("→", "Avançar", lambda: self.navigate_action("forward"))
        self.reload = self.button("↻", "Recarregar", lambda: self.navigate_action("reload"))
        home = self.button("⌂", "Página inicial (Alt+Home)", self.open_home)
        for button in (self.back, self.forward, self.reload, home):
            navigation.addWidget(button)
        navigation.addStretch()
        header.addLayout(navigation)

        self.address = QLineEdit()
        self.address.setAccessibleName("Endereço ou busca")
        self.address.setPlaceholderText("Endereço ou busca")
        self.address.setClearButtonEnabled(True)
        self.address.returnPressed.connect(self.open_address)
        header.addWidget(self.address)
        header.addWidget(self.button("+  Nova aba", "Nova aba (Ctrl+T)", self.new_tab, "primary"))
        header.addWidget(QLabel("Abas", objectName="muted"))
        self.tab_list = QListWidget()
        self.tab_list.setAccessibleName("Abas abertas")
        self.tab_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tab_list.setIconSize(QSize(20, 20))
        self.tab_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tab_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_list.customContextMenuRequested.connect(self.tab_menu)
        self.tab_list.currentRowChanged.connect(self.select_tab)
        side.addWidget(self.tab_list, 1)

        self.sidebar_footer = QWidget()
        bottom = QVBoxLayout(self.sidebar_footer)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(10)
        footer = QHBoxLayout()
        self.close_button = self.button("Fechar aba", "Fechar aba (Ctrl+W)", self.close_current)
        footer.addWidget(self.close_button)
        self.privacy_button = self.button("Privacidade", "Opções de privacidade", self.privacy_menu)
        footer.addWidget(self.privacy_button)
        bottom.addLayout(footer)
        self.status = QLabel(objectName="muted")
        bottom.addWidget(self.status)
        side.addWidget(self.sidebar_footer)
        self.compact_new_tab = self.button("+", "Nova aba (Ctrl+T)", self.new_tab)
        side.addWidget(self.compact_new_tab, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.compact_new_tab.hide()
        self.settings_button = self.button("⚙", "Configurações", self.open_preferences)
        side.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        surface = QFrame(objectName="surface")
        main = QVBoxLayout(surface)
        self.surface_layout = main
        main.setContentsMargins(6, 6, 6, 6)
        self.stack = QStackedWidget()
        main.addWidget(self.stack)
        layout.addWidget(surface, 1)

        for keys, callback in (
            ("Ctrl+L", self.focus_address), ("Ctrl+T", self.new_tab),
            ("Ctrl+W", self.close_current), ("Ctrl+R", lambda: self.navigate_action("reload")),
            ("F5", lambda: self.navigate_action("reload")),
            ("Alt+Left", lambda: self.navigate_action("back")),
            ("Alt+Right", lambda: self.navigate_action("forward")),
            ("Alt+Home", self.open_home),
            ("Ctrl+Tab", lambda: self.step_tab(1)),
            ("Ctrl+Shift+Tab", lambda: self.step_tab(-1)),
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(keys))
            action.triggered.connect(callback)
            self.addAction(action)

        self.exit_fullscreen_action = QAction(self)
        self.exit_fullscreen_action.setShortcut(QKeySequence("Esc"))
        self.exit_fullscreen_action.setEnabled(False)
        self.exit_fullscreen_action.triggered.connect(self.exit_fullscreen)
        self.addAction(self.exit_fullscreen_action)

        self.timer = QTimer(self, interval=15000)
        self.timer.timeout.connect(self.discard_idle)
        self.timer.start()
        self.apply_preferences()
        self.new_tab()
        startup = page_url(self.preferences["startup"])
        if not startup.isEmpty():
            self.navigate_to(startup)
        if self.preferences["update_checks"]:
            self.initial_update_timer.start()

    def open_preferences(self):
        PreferencesDialog(self).exec()

    def save_preferences(self, values):
        for key, value in values.items():
            self.settings_store.setValue(key, value)
        self.settings_store.sync()
        self.preferences = values
        self.apply_preferences()

    def apply_preferences(self):
        if self.preferences["update_checks"]:
            if not self.update_timer.isActive():
                self.update_timer.start()
        else:
            self.update_timer.stop()
            self.initial_update_timer.stop()
            if self.update_reply is not None and not self.manual_update_check:
                self.update_reply.abort()
        self.privacy.block_third_party = self.preferences["third_party"]
        settings = self.profile.settings()
        attributes = QWebEngineSettings.WebAttribute
        settings.setAttribute(attributes.JavascriptEnabled, self.preferences["javascript"])
        settings.setAttribute(attributes.AutoLoadImages, self.preferences["images"])
        for tab in self.tabs:
            if tab.view is not None:
                tab.view.setZoomFactor(self.preferences["zoom"] / 100)

    def check_updates(self, manual=False):
        if not manual and not self.preferences["update_checks"]:
            return
        self.manual_update_check |= manual
        if self.update_reply is not None:
            return
        url = QUrl(f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest")
        request = QNetworkRequest(url)
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", b"EAGLE-Update")
        request.setTransferTimeout(10000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        reply = self.update_network.get(request)
        self.update_reply = reply
        reply.finished.connect(lambda: self.finish_update_check(reply))

    def finish_update_check(self, reply):
        manual = self.manual_update_check
        self.manual_update_check = False
        self.update_reply = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        payload = None
        if status == 200 and reply.error() == QNetworkReply.NetworkError.NoError:
            try:
                payload = json.loads(bytes(reply.readAll()))
            except (ValueError, UnicodeError):
                pass
        reply.deleteLater()
        release = available_release(payload)
        if release:
            version, url = release
            if self.update_notice is None and (manual or version != self.last_update_notice):
                self.last_update_notice = version
                self.show_update_notice(version, url)
        elif manual:
            message = "O GitHub ainda não disponibilizou uma versão pública do EAGLE."
            if isinstance(payload, dict):
                message = "Nenhuma atualização compatível disponível."
            elif status != 404:
                message = "Não foi possível consultar o GitHub. Tente novamente mais tarde."
            QMessageBox.information(QApplication.activeModalWidget() or self, "Atualizações do EAGLE", message)

    def show_update_notice(self, version, url):
        parent = QApplication.activeModalWidget()
        notice = QMessageBox(parent or self)
        if parent is not None:
            notice.setWindowModality(Qt.WindowModality.WindowModal)
        notice.setWindowTitle("Atualização do EAGLE")
        notice.setText(f"O EAGLE {version} está disponível.")
        download = notice.addButton("Ver versão no GitHub", QMessageBox.ButtonRole.AcceptRole)
        notice.addButton("Depois", QMessageBox.ButtonRole.RejectRole)
        self.update_notice = notice

        def finished(_result):
            self.update_notice = None
            if notice.clickedButton() is download:
                QDesktopServices.openUrl(url)
            notice.deleteLater()

        notice.finished.connect(finished)
        notice.show()

    def open_home(self):
        url = page_url(self.preferences["home"])
        if url.isEmpty():
            self.new_tab()
        else:
            self.navigate_to(url)

    def button(self, text, tip, callback, name=""):
        button = QPushButton(text, objectName=name)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.clicked.connect(callback)
        return button

    def focus_address(self):
        if self.fullscreen_tab is not None:
            self.exit_fullscreen()
            if self.fullscreen_tab is not None:
                self.restore_window()
        self.set_sidebar_compact(False)
        self.address.setFocus()
        self.address.selectAll()

    def toggle_sidebar(self):
        self.set_sidebar_compact(not self.sidebar_compact)

    def set_sidebar_compact(self, compact):
        if compact == self.sidebar_compact:
            return
        self.sidebar_compact = compact
        self.sidebar_header.setVisible(not compact)
        self.sidebar_footer.setVisible(not compact)
        self.compact_new_tab.setVisible(compact)
        self.rail.setFixedWidth(74 if compact else 270)
        self.rail.layout().setContentsMargins(10 if compact else 16, 17,
                                              10 if compact else 16, 16)
        label = "Expandir barra lateral" if compact else "Recolher barra lateral"
        self.sidebar_toggle.setText("»" if compact else "«")
        self.sidebar_toggle.setToolTip(label)
        self.sidebar_toggle.setAccessibleName(label)
        self.tab_list.setObjectName("compactTabs" if compact else "")
        self.tab_list.style().unpolish(self.tab_list)
        self.tab_list.style().polish(self.tab_list)
        self.tab_list.setViewMode(QListView.ViewMode.IconMode if compact else QListView.ViewMode.ListMode)
        self.tab_list.setFlow(QListView.Flow.TopToBottom)
        self.tab_list.setWrapping(False)
        self.tab_list.setMovement(QListView.Movement.Static)
        self.tab_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.tab_list.setGridSize(QSize(54, 44) if compact else QSize())
        self.tab_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if compact else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        for tab in self.tabs:
            self.refresh_tab(tab)
        self.tab_list.scrollToItem(self.tab_list.currentItem())

    def current_tab(self):
        row = self.tab_list.currentRow()
        return self.tabs[row] if 0 <= row < len(self.tabs) else None

    def new_tab(self):
        start = QWidget()
        content = QVBoxLayout(start)
        content.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.setSpacing(12)
        content.addWidget(QLabel("Nova aba", objectName="headline"))
        content.addWidget(QLabel("Digite um endereço ou faça uma busca.", objectName="muted"))
        content.addWidget(self.button("Abrir barra de endereços", "Ctrl+L", self.focus_address))
        tab = Tab(QListWidgetItem("Nova aba"), start)
        self.tabs.append(tab)
        self.stack.addWidget(start)
        self.tab_list.addItem(tab.item)
        self.tab_list.setCurrentItem(tab.item)
        self.focus_address()
        return tab

    def make_view(self, tab):
        view = QWebEngineView()
        page = QWebEnginePage(self.profile, view)
        view.setPage(page)
        view.setZoomFactor(self.preferences["zoom"] / 100)
        page.permissionRequested.connect(lambda permission: self.site_permission(tab, permission))
        page.fullScreenRequested.connect(lambda request: self.handle_fullscreen(tab, request))
        page.newWindowRequested.connect(self.open_window_request)
        page.windowCloseRequested.connect(lambda: self.close_tab(tab))
        view.loadStarted.connect(lambda: self.refresh_tab(tab))
        view.loadFinished.connect(lambda _: self.refresh_tab(tab))
        view.titleChanged.connect(lambda _: self.refresh_tab(tab))
        view.iconChanged.connect(lambda _: self.refresh_tab(tab))
        view.urlChanged.connect(lambda _: self.refresh_tab(tab))
        page.lifecycleStateChanged.connect(lambda _: self.refresh_tab(tab))

        start = tab.widget
        tab.widget = view
        tab.view = view
        self.stack.addWidget(view)
        if tab is self.current_tab():
            self.stack.setCurrentWidget(view)
        self.stack.removeWidget(start)
        start.deleteLater()

    def site_permission(self, tab, permission):
        kind = {
            "MediaAudioCapture": "microfone", "MediaVideoCapture": "câmera",
            "MediaAudioVideoCapture": "câmera e microfone",
            "Geolocation": "localização", "Notifications": "notificações",
        }.get(permission.permissionType().name)
        if self.preferences["permissions"] and tab is self.current_tab() and kind:
            reply = QMessageBox.question(self, "Permissão do site",
                                         f"{permission.origin().toDisplayString()} solicita acesso a {kind}. Permitir?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                permission.grant()
                return
        permission.deny()

    def handle_fullscreen(self, tab, request):
        if request.toggleOn():
            if tab is not self.current_tab() or self.fullscreen_tab is not None:
                request.reject()
                return
            self.fullscreen_tab = tab
            self.previous_window_state = self.windowState()
            request.accept()
            self.rail.hide()
            self.shell_layout.setContentsMargins(0, 0, 0, 0)
            self.surface_layout.setContentsMargins(0, 0, 0, 0)
            self.exit_fullscreen_action.setEnabled(True)
            self.showFullScreen()
            return

        request.accept()
        if self.fullscreen_tab is tab:
            self.restore_window()

    def exit_fullscreen(self):
        if self.fullscreen_tab is not None:
            self.fullscreen_tab.view.page().triggerAction(QWebEnginePage.WebAction.ExitFullScreen)

    def restore_window(self):
        self.fullscreen_tab = None
        self.exit_fullscreen_action.setEnabled(False)
        self.rail.show()
        self.shell_layout.setContentsMargins(12, 12, 12, 12)
        self.surface_layout.setContentsMargins(6, 6, 6, 6)
        if self.previous_window_state & Qt.WindowState.WindowMaximized:
            self.showMaximized()
        else:
            self.showNormal()

    def open_window_request(self, request):
        if not request.isUserInitiated():
            return
        previous = self.current_tab()
        tab = self.new_tab()
        self.make_view(tab)
        request.openIn(tab.view.page())
        if request.destination() == request.DestinationType.InNewBackgroundTab and previous:
            self.tab_list.setCurrentItem(previous.item)
        elif tab is self.current_tab():
            self.set_sidebar_compact(self.preferences["collapse"])
            tab.view.setFocus()

    def open_address(self):
        self.navigate_to(address_url(self.address.text(), self.preferences["search"]))

    def navigate_to(self, url):
        tab = self.current_tab()
        if url.isEmpty() or tab is None:
            return
        if tab.view is None:
            self.make_view(tab)
        tab.view.setUrl(url)
        self.set_sidebar_compact(self.preferences["collapse"])
        tab.view.setFocus()

    def select_tab(self, row):
        if not 0 <= row < len(self.tabs):
            return
        tab = self.tabs[row]
        if self.fullscreen_tab is not None and self.fullscreen_tab is not tab:
            self.exit_fullscreen()
            if self.fullscreen_tab is not None:
                self.restore_window()
        previous = self.stack.currentWidget()
        now = monotonic()
        for candidate in self.tabs:
            if candidate.widget is previous:
                candidate.inactive_since = now
                break
        self.stack.setCurrentWidget(tab.widget)
        if tab.view:
            tab.view.page().setLifecycleState(Lifecycle.Active)
        self.set_sidebar_compact(tab.view is not None and self.preferences["collapse"])
        self.refresh_tab(tab, update_address=True)

    def step_tab(self, offset):
        self.tab_list.setCurrentRow((self.tab_list.currentRow() + offset) % len(self.tabs))

    def refresh_tab(self, tab, update_address=False):
        if tab not in self.tabs:
            return
        view = tab.view
        url = view.url() if view else QUrl()
        label = (view.title() or url.host()) if view else ""
        if tab.keep_active:
            label = "◆ " + (label or "Nova aba")
        elif view and view.page().lifecycleState() != Lifecycle.Active:
            label = "◌ " + (label or "Aba em repouso")
        label = label or "Nova aba"
        icon = view.icon() if view else self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        if icon.isNull() and (not view or view.page().lifecycleState() == Lifecycle.Active):
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        if not icon.isNull():
            tab.item.setIcon(icon)
        tab.item.setText("" if self.sidebar_compact else label)
        tab.item.setData(Qt.ItemDataRole.AccessibleTextRole, label)
        tab.item.setToolTip(label + ("\n" + url.host() if url.host() else ""))
        if tab is self.current_tab():
            if update_address or not self.address.hasFocus():
                self.address.setText(url.toDisplayString())
            self.back.setEnabled(bool(view and view.history().canGoBack()))
            self.forward.setEnabled(bool(view and view.history().canGoForward()))
            self.reload.setEnabled(view is not None)
        self.refresh_status()

    def refresh_status(self):
        asleep = sum(
            tab.view is not None and tab.view.page().lifecycleState() != Lifecycle.Active
            for tab in self.tabs
        )
        self.status.setText(f"Em repouso · {asleep}")

    def navigate_action(self, action):
        tab = self.current_tab()
        if tab and tab.view:
            getattr(tab.view, action)()

    def close_current(self):
        tab = self.current_tab()
        if tab:
            self.close_tab(tab)

    def close_tab(self, tab):
        if tab not in self.tabs:
            return
        if self.fullscreen_tab is tab:
            self.restore_window()
        row = self.tabs.index(tab)
        current = self.current_tab()
        self.tab_list.blockSignals(True)
        self.tabs.pop(row)
        self.tab_list.takeItem(row)
        self.stack.removeWidget(tab.widget)
        tab.widget.deleteLater()
        self.tab_list.blockSignals(False)
        if not self.tabs:
            self.new_tab()
        else:
            next_tab = current if current is not tab else self.tabs[min(row, len(self.tabs) - 1)]
            self.tab_list.setCurrentItem(next_tab.item)
            if current is tab:
                self.select_tab(self.tabs.index(next_tab))
        self.refresh_status()

    def discard_idle(self):
        if not self.preferences["memory_saver"]:
            return
        now = monotonic()
        for tab in self.tabs:
            if tab is self.current_tab() or tab.keep_active or tab.view is None:
                continue
            if now - tab.inactive_since < self.preferences["idle_minutes"] * 60:
                continue
            self.rest_tab(tab)

    def can_rest(self, tab):
        if tab is self.current_tab() or tab.view is None or tab.keep_active:
            return False
        page = tab.view.page()
        return (not page.recentlyAudible() and not page.isLoading()
                and page.recommendedState().value > page.lifecycleState().value)

    def rest_tab(self, tab):
        if not self.can_rest(tab):
            return
        page = tab.view.page()
        page.setLifecycleState(page.recommendedState())
        # Qt can only determine whether discarding is safe once the page is frozen.
        if page.recommendedState() == Lifecycle.Discarded:
            page.setLifecycleState(Lifecycle.Discarded)

    def tab_menu(self, point):
        row = self.tab_list.indexAt(point).row()
        if row < 0:
            return
        tab = self.tabs[row]
        menu = QMenu(self)
        keep = menu.addAction("Manter aba ativa")
        keep.setCheckable(True)
        keep.setChecked(tab.keep_active)
        sleep = menu.addAction("Recolher agora")
        sleep.setEnabled(self.can_rest(tab))
        close = menu.addAction("Fechar aba")
        chosen = menu.exec(self.tab_list.viewport().mapToGlobal(point))
        if chosen is keep:
            tab.keep_active = keep.isChecked()
            if tab.keep_active and tab.view:
                tab.view.page().setLifecycleState(Lifecycle.Active)
            self.refresh_tab(tab)
        elif chosen is sleep:
            self.rest_tab(tab)
        elif chosen is close:
            self.close_tab(tab)

    def privacy_menu(self):
        menu = QMenu(self)
        menu.addAction("Histórico e cookies desta sessão em memória").setEnabled(False)
        tab = self.current_tab()
        url = tab.view.url() if tab and tab.view else QUrl()
        host = url.host() if url.scheme() in ("http", "https") else ""
        exception = menu.addAction(f"Permitir cookies de terceiros em {host}" if host
                                   else "Permitir cookies de terceiros neste site")
        exception.setEnabled(bool(host) and self.privacy.block_third_party)
        exception.setCheckable(True)
        exception.setChecked(host in self.privacy.exceptions)
        chosen = menu.exec(self.privacy_button.mapToGlobal(self.privacy_button.rect().topLeft()))
        if chosen is exception and host:
            self.privacy.toggle(host)
            tab.view.reload()

    def download(self, request):
        default = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        folder = Path(self.preferences["download_folder"] or default)
        target = folder / Path(request.suggestedFileName()).name
        if self.preferences["ask_download"] or not folder.is_dir() or not folder.is_absolute():
            filename, _ = QFileDialog.getSaveFileName(self, "Salvar download", str(target))
            if not filename:
                request.cancel()
                return
            target = Path(filename)
        else:
            counter = 1
            original = target
            while target.exists():
                target = original.with_stem(f"{original.stem} ({counter})")
                counter += 1
        request.setDownloadDirectory(str(target.parent))
        request.setDownloadFileName(target.name)
        request.accept()

    def closeEvent(self, event):
        self.timer.stop()
        self.update_timer.stop()
        self.initial_update_timer.stop()
        if self.update_reply is not None:
            self.manual_update_check = False
            self.update_reply.abort()
        for tab in self.tabs:
            if tab.view:
                tab.view.deleteLater()
        self.tabs.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.profile.deleteLater()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("EAGLE")
    app.setApplicationVersion(APP_VERSION)
    window = Eagle()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
