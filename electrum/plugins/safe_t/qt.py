import threading
from functools import partial
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt, pyqtSignal, QRegExp
from PyQt5.QtGui import QRegExpValidator
from PyQt5.QtWidgets import (QVBoxLayout, QLabel, QGridLayout, QPushButton,
                             QHBoxLayout, QButtonGroup, QGroupBox,
                             QTextEdit, QLineEdit, QRadioButton, QCheckBox, QWidget,
                             QMessageBox, QFileDialog, QSlider, QTabWidget)

from electrum.gui.qt.util import (WindowModalDialog, WWLabel, Buttons, CancelButton,
                                  OkButton, CloseButton, getOpenFileName, ChoiceWidget)
from electrum.i18n import _
from electrum.plugin import hook
from electrum.logging import Logger

from ..hw_wallet.qt import QtHandlerBase, QtPluginBase
from ..hw_wallet.plugin import only_hook_if_libraries_available
from .safe_t import SafeTPlugin, TIM_NEW, TIM_RECOVER, TIM_MNEMONIC, TIM_PRIVKEY

from electrum.gui.qt.wizard.wallet import WCScriptAndDerivation, WCHWUnlock, WCHWXPub
from electrum.gui.qt.wizard.wizard import WizardComponent

if TYPE_CHECKING:
    from electrum.gui.qt.wizard.wallet import QENewWalletWizard

PASSPHRASE_HELP_SHORT =_(
    "Passphrases allow you to access new wallets, each "
    "hidden behind a particular case-sensitive passphrase.")
PASSPHRASE_HELP = PASSPHRASE_HELP_SHORT + "  " + _(
    "You need to create a separate Electrum Atom wallet for each passphrase "
    "you use as they each generate different addresses.  Changing "
    "your passphrase does not lose other wallets, each is still "
    "accessible behind its own passphrase.")
RECOMMEND_PIN = _(
    "You should enable PIN protection.  Your PIN is the only protection "
    "for your bitcoins if your device is lost or stolen.")
PASSPHRASE_NOT_PIN = _(
    "If you forget a passphrase you will be unable to access any "
    "bitcoins in the wallet behind it.  A passphrase is not a PIN. "
    "Only change this if you are sure you understand it.")


class QtHandler(QtHandlerBase):

    pin_signal = pyqtSignal(object, object)

    def __init__(self, win, pin_matrix_widget_class, device):
        super(QtHandler, self).__init__(win, device)
        self.pin_signal.connect(self.pin_dialog)
        self.pin_matrix_widget_class = pin_matrix_widget_class

    def get_pin(self, msg, *, show_strength=True):
        self.done.clear()
        self.pin_signal.emit(msg, show_strength)
        self.done.wait()
        return self.response

    def pin_dialog(self, msg, show_strength):
        # Needed e.g. when resetting a device
        self.clear_dialog()
        dialog = WindowModalDialog(self.top_level_window(), _("Enter PIN"))
        matrix = self.pin_matrix_widget_class(show_strength)
        vbox = QVBoxLayout()
        vbox.addWidget(QLabel(msg))
        vbox.addWidget(matrix)
        vbox.addLayout(Buttons(CancelButton(dialog), OkButton(dialog)))
        dialog.setLayout(vbox)
        dialog.exec_()
        self.response = str(matrix.get_value())
        self.done.set()


class QtPlugin(QtPluginBase):
    # Derived classes must provide the following class-static variables:
    #   icon_file
    #   pin_matrix_widget_class

    @only_hook_if_libraries_available
    @hook
    def receive_menu(self, menu, addrs, wallet):
        if len(addrs) != 1:
            return
        for keystore in wallet.get_keystores():
            if type(keystore) == self.keystore_class:
                def show_address(keystore=keystore):
                    keystore.thread.add(partial(self.show_address, wallet, addrs[0], keystore))
                device_name = "{} ({})".format(self.device, keystore.label)
                menu.addAction(_("Show on {}").format(device_name), show_address)

    def show_settings_dialog(self, window, keystore):
        def connect():
            device_id = self.choose_device(window, keystore)
            return device_id
        def show_dialog(device_id):
            if device_id:
                SettingsDialog(window, self, keystore, device_id).exec_()
        keystore.thread.add(connect, on_success=show_dialog)


def clean_text(widget):
    text = widget.toPlainText().strip()
    return ' '.join(text.split())


class SafeTInitLayout(QVBoxLayout):
    validChanged = pyqtSignal([bool], arguments=['valid'])

    def __init__(self, method, device):
        super().__init__()

        self.method = method

        label = QLabel(_("Enter a label to name your device:"))
        self.label_e = QLineEdit()
        hl = QHBoxLayout()
        hl.addWidget(label)
        hl.addWidget(self.label_e)
        hl.addStretch(1)
        self.addLayout(hl)

        if method in [TIM_NEW, TIM_RECOVER]:
            gb = QGroupBox()
            hbox1 = QHBoxLayout()
            gb.setLayout(hbox1)
            self.addWidget(gb)
            gb.setTitle(_("Select your seed length:"))
            self.bg = QButtonGroup()
            for i, count in enumerate([12, 18, 24]):
                rb = QRadioButton(gb)
                rb.setText(_("{:d} words").format(count))
                self.bg.addButton(rb)
                self.bg.setId(rb, i)
                hbox1.addWidget(rb)
                rb.setChecked(True)
            self.cb_pin = QCheckBox(_('Enable PIN protection'))
            self.cb_pin.setChecked(True)
        else:
            self.text_e = QTextEdit()
            self.text_e.setMaximumHeight(60)
            if method == TIM_MNEMONIC:
                msg = _("Enter your BIP39 mnemonic:")
                # TODO: no validation?
            else:
                msg = _("Enter the master private key beginning with xprv:")

                def set_enabled():
                    from electrum.bip32 import is_xprv
                    self.validChanged.emit(is_xprv(clean_text(self.text_e)))
                self.text_e.textChanged.connect(set_enabled)

            self.addWidget(QLabel(msg))
            self.addWidget(self.text_e)
            self.pin = QLineEdit()
            self.pin.setValidator(QRegExpValidator(QRegExp('[1-9]{0,9}')))
            self.pin.setMaximumWidth(100)
            hbox_pin = QHBoxLayout()
            hbox_pin.addWidget(QLabel(_("Enter your PIN (digits 1-9):")))
            hbox_pin.addWidget(self.pin)
            hbox_pin.addStretch(1)

        if method in [TIM_NEW, TIM_RECOVER]:
            self.addWidget(WWLabel(RECOMMEND_PIN))
            self.addWidget(self.cb_pin)
        else:
            self.addLayout(hbox_pin)

        passphrase_msg = WWLabel(PASSPHRASE_HELP_SHORT)
        passphrase_warning = WWLabel(PASSPHRASE_NOT_PIN)
        passphrase_warning.setStyleSheet("color: red")
        self.cb_phrase = QCheckBox(_('Enable passphrases'))
        self.cb_phrase.setChecked(False)
        self.addWidget(passphrase_msg)
        self.addWidget(passphrase_warning)
        self.addWidget(self.cb_phrase)

    def get_settings(self):
        if self.method in [TIM_NEW, TIM_RECOVER]:
            item = self.bg.checkedId()
            pin = self.cb_pin.isChecked()
        else:
            item = ' '.join(str(clean_text(self.text_e)).split())
            pin = str(self.pin.text())

        return item, self.label_e.text(), pin, self.cb_phrase.isChecked()


class Plugin(SafeTPlugin, QtPlugin):
    icon_unpaired = "safe-t_unpaired.png"
    icon_paired = "safe-t.png"

    def create_handler(self, window):
        return QtHandler(window, self.pin_matrix_widget_class(), self.device)

    @classmethod
    def pin_matrix_widget_class(self):
        from safetlib.qt.pinmatrix import PinMatrixWidget
        return PinMatrixWidget

    @hook
    def init_wallet_wizard(self, wizard: 'QENewWalletWizard'):
        self.extend_wizard(wizard)

    # insert safe_t pages in new wallet wizard
    def extend_wizard(self, wizard: 'QENewWalletWizard'):
        super().extend_wizard(wizard)
        views = {
            'safet_start': {'gui': WCScriptAndDerivation},
            'safet_xpub': {'gui': WCHWXPub},
            'safet_not_initialized': {'gui': WCSafeTInitMethod},
            'safet_choose_new_recover': {'gui': WCSafeTInitParams},
            'safet_do_init': {'gui': WCSafeTInit},
            'safet_unlock': {'gui': WCHWUnlock}
        }
        wizard.navmap_merge(views)


class SettingsDialog(WindowModalDialog):
    '''This dialog doesn't require a device be paired with a wallet.
    We want users to be able to wipe a device even if they've forgotten
    their PIN.'''

    def __init__(self, window, plugin, keystore, device_id):
        title = _("{} Settings").format(plugin.device)
        super(SettingsDialog, self).__init__(window, title)
        self.setMaximumWidth(540)

        devmgr = plugin.device_manager()
        config = devmgr.config
        handler = keystore.handler
        thread = keystore.thread
        hs_cols, hs_rows = (128, 64)

        def invoke_client(method, *args, **kw_args):
            unpair_after = kw_args.pop('unpair_after', False)

            def task():
                client = devmgr.client_by_id(device_id)
                if not client:
                    raise RuntimeError("Device not connected")
                if method:
                    getattr(client, method)(*args, **kw_args)
                if unpair_after:
                    devmgr.unpair_id(device_id)
                return client.features

            thread.add(task, on_success=update)

        def update(features):
            self.features = features
            set_label_enabled()
            if features.bootloader_hash:
                bl_hash = features.bootloader_hash.hex()
                bl_hash = "\n".join([bl_hash[:32], bl_hash[32:]])
            else:
                bl_hash = "N/A"
            noyes = [_("No"), _("Yes")]
            endis = [_("Enable Passphrases"), _("Disable Passphrases")]
            disen = [_("Disabled"), _("Enabled")]
            setchange = [_("Set a PIN"), _("Change PIN")]

            version = "%d.%d.%d" % (features.major_version,
                                    features.minor_version,
                                    features.patch_version)

            device_label.setText(features.label)
            pin_set_label.setText(noyes[features.pin_protection])
            passphrases_label.setText(disen[features.passphrase_protection])
            bl_hash_label.setText(bl_hash)
            label_edit.setText(features.label)
            device_id_label.setText(features.device_id)
            initialized_label.setText(noyes[features.initialized])
            version_label.setText(version)
            clear_pin_button.setVisible(features.pin_protection)
            clear_pin_warning.setVisible(features.pin_protection)
            pin_button.setText(setchange[features.pin_protection])
            pin_msg.setVisible(not features.pin_protection)
            passphrase_button.setText(endis[features.passphrase_protection])
            language_label.setText(features.language)

        def set_label_enabled():
            label_apply.setEnabled(label_edit.text() != self.features.label)

        def rename():
            invoke_client('change_label', label_edit.text())

        def toggle_passphrase():
            title = _("Confirm Toggle Passphrase Protection")
            currently_enabled = self.features.passphrase_protection
            if currently_enabled:
                msg = _("After disabling passphrases, you can only pair this "
                        "Electrum Atom wallet if it had an empty passphrase.  "
                        "If its passphrase was not empty, you will need to "
                        "create a new wallet.  You can use this wallet again "
                        "at any time by re-enabling passphrases and entering "
                        "its passphrase.")
            else:
                msg = _("Your current Electrum Atom wallet can only be used with "
                        "an empty passphrase.  You must create a separate "
                        "wallet for other passphrases as each one generates "
                        "a new set of addresses.")
            msg += "\n\n" + _("Are you sure you want to proceed?")
            if not self.question(msg, title=title):
                return
            invoke_client('toggle_passphrase', unpair_after=currently_enabled)

        def change_homescreen():
            filename = getOpenFileName(
                parent=self,
                title=_("Choose Homescreen"),
                config=config,
            )
            if not filename:
                return  # user cancelled

            if filename.endswith('.toif'):
                img = open(filename, 'rb').read()
                if img[:8] != b'TOIf\x90\x00\x90\x00':
                    handler.show_error('File is not a TOIF file with size of 144x144')
                    return
            else:
                from PIL import Image # FIXME
                im = Image.open(filename)
                if im.size != (128, 64):
                    handler.show_error('Image must be 128 x 64 pixels')
                    return
                im = im.convert('1')
                pix = im.load()
                img = bytearray(1024)
                for j in range(64):
                    for i in range(128):
                        if pix[i, j]:
                            o = (i + j * 128)
                            img[o // 8] |= (1 << (7 - o % 8))
                img = bytes(img)
            invoke_client('change_homescreen', img)

        def clear_homescreen():
            invoke_client('change_homescreen', b'\x00')

        def set_pin():
            invoke_client('set_pin', remove=False)

        def clear_pin():
            invoke_client('set_pin', remove=True)

        def wipe_device():
            wallet = window.wallet
            if wallet and sum(wallet.get_balance()):
                title = _("Confirm Device Wipe")
                msg = _("Are you SURE you want to wipe the device?\n"
                        "Your wallet still has bitcoins in it!")
                if not self.question(msg, title=title,
                                     icon=QMessageBox.Critical):
                    return
            invoke_client('wipe_device', unpair_after=True)

        def slider_moved():
            mins = timeout_slider.sliderPosition()
            timeout_minutes.setText(_("{:2d} minutes").format(mins))

        def slider_released():
            config.set_session_timeout(timeout_slider.sliderPosition() * 60)

        # Information tab
        info_tab = QWidget()
        info_layout = QVBoxLayout(info_tab)
        info_glayout = QGridLayout()
        info_glayout.setColumnStretch(2, 1)
        device_label = QLabel()
        pin_set_label = QLabel()
        passphrases_label = QLabel()
        version_label = QLabel()
        device_id_label = QLabel()
        bl_hash_label = QLabel()
        bl_hash_label.setWordWrap(True)
        language_label = QLabel()
        initialized_label = QLabel()
        rows = [
            (_("Device Label"), device_label),
            (_("PIN set"), pin_set_label),
            (_("Passphrases"), passphrases_label),
            (_("Firmware Version"), version_label),
            (_("Device ID"), device_id_label),
            (_("Bootloader Hash"), bl_hash_label),
            (_("Language"), language_label),
            (_("Initialized"), initialized_label),
        ]
        for row_num, (label, widget) in enumerate(rows):
            info_glayout.addWidget(QLabel(label), row_num, 0)
            info_glayout.addWidget(widget, row_num, 1)
        info_layout.addLayout(info_glayout)

        # Settings tab
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        settings_glayout = QGridLayout()

        # Settings tab - Label
        label_msg = QLabel(_("Name this {}.  If you have multiple devices "
                             "their labels help distinguish them.")
                           .format(plugin.device))
        label_msg.setWordWrap(True)
        label_label = QLabel(_("Device Label"))
        label_edit = QLineEdit()
        label_edit.setMinimumWidth(150)
        label_edit.setMaxLength(plugin.MAX_LABEL_LEN)
        label_apply = QPushButton(_("Apply"))
        label_apply.clicked.connect(rename)
        label_edit.textChanged.connect(set_label_enabled)
        settings_glayout.addWidget(label_label, 0, 0)
        settings_glayout.addWidget(label_edit, 0, 1, 1, 2)
        settings_glayout.addWidget(label_apply, 0, 3)
        settings_glayout.addWidget(label_msg, 1, 1, 1, -1)

        # Settings tab - PIN
        pin_label = QLabel(_("PIN Protection"))
        pin_button = QPushButton()
        pin_button.clicked.connect(set_pin)
        settings_glayout.addWidget(pin_label, 2, 0)
        settings_glayout.addWidget(pin_button, 2, 1)
        pin_msg = QLabel(_("PIN protection is strongly recommended.  "
                           "A PIN is your only protection against someone "
                           "stealing your bitcoins if they obtain physical "
                           "access to your {}.").format(plugin.device))
        pin_msg.setWordWrap(True)
        pin_msg.setStyleSheet("color: red")
        settings_glayout.addWidget(pin_msg, 3, 1, 1, -1)

        # Settings tab - Homescreen
        homescreen_label = QLabel(_("Homescreen"))
        homescreen_change_button = QPushButton(_("Change..."))
        homescreen_clear_button = QPushButton(_("Reset"))
        homescreen_change_button.clicked.connect(change_homescreen)
        try:
            import PIL
        except ImportError:
            homescreen_change_button.setDisabled(True)
            homescreen_change_button.setToolTip(
                _("Required package 'PIL' is not available - Please install it.")
            )
        homescreen_clear_button.clicked.connect(clear_homescreen)
        homescreen_msg = QLabel(_("You can set the homescreen on your "
                                  "device to personalize it.  You must "
                                  "choose a {} x {} monochrome black and "
                                  "white image.").format(hs_cols, hs_rows))
        homescreen_msg.setWordWrap(True)
        settings_glayout.addWidget(homescreen_label, 4, 0)
        settings_glayout.addWidget(homescreen_change_button, 4, 1)
        settings_glayout.addWidget(homescreen_clear_button, 4, 2)
        settings_glayout.addWidget(homescreen_msg, 5, 1, 1, -1)

        # Settings tab - Session Timeout
        timeout_label = QLabel(_("Session Timeout"))
        timeout_minutes = QLabel()
        timeout_slider = QSlider(Qt.Horizontal)
        timeout_slider.setRange(1, 60)
        timeout_slider.setSingleStep(1)
        timeout_slider.setTickInterval(5)
        timeout_slider.setTickPosition(QSlider.TicksBelow)
        timeout_slider.setTracking(True)
        timeout_msg = QLabel(
            _("Clear the session after the specified period "
              "of inactivity.  Once a session has timed out, "
              "your PIN and passphrase (if enabled) must be "
              "re-entered to use the device."))
        timeout_msg.setWordWrap(True)
        timeout_slider.setSliderPosition(config.get_session_timeout() // 60)
        slider_moved()
        timeout_slider.valueChanged.connect(slider_moved)
        timeout_slider.sliderReleased.connect(slider_released)
        settings_glayout.addWidget(timeout_label, 6, 0)
        settings_glayout.addWidget(timeout_slider, 6, 1, 1, 3)
        settings_glayout.addWidget(timeout_minutes, 6, 4)
        settings_glayout.addWidget(timeout_msg, 7, 1, 1, -1)
        settings_layout.addLayout(settings_glayout)
        settings_layout.addStretch(1)

        # Advanced tab
        advanced_tab = QWidget()
        advanced_layout = QVBoxLayout(advanced_tab)
        advanced_glayout = QGridLayout()

        # Advanced tab - clear PIN
        clear_pin_button = QPushButton(_("Disable PIN"))
        clear_pin_button.clicked.connect(clear_pin)
        clear_pin_warning = QLabel(
            _("If you disable your PIN, anyone with physical access to your "
              "{} device can spend your bitcoins.").format(plugin.device))
        clear_pin_warning.setWordWrap(True)
        clear_pin_warning.setStyleSheet("color: red")
        advanced_glayout.addWidget(clear_pin_button, 0, 2)
        advanced_glayout.addWidget(clear_pin_warning, 1, 0, 1, 5)

        # Advanced tab - toggle passphrase protection
        passphrase_button = QPushButton()
        passphrase_button.clicked.connect(toggle_passphrase)
        passphrase_msg = WWLabel(PASSPHRASE_HELP)
        passphrase_warning = WWLabel(PASSPHRASE_NOT_PIN)
        passphrase_warning.setStyleSheet("color: red")
        advanced_glayout.addWidget(passphrase_button, 3, 2)
        advanced_glayout.addWidget(passphrase_msg, 4, 0, 1, 5)
        advanced_glayout.addWidget(passphrase_warning, 5, 0, 1, 5)

        # Advanced tab - wipe device
        wipe_device_button = QPushButton(_("Wipe Device"))
        wipe_device_button.clicked.connect(wipe_device)
        wipe_device_msg = QLabel(
            _("Wipe the device, removing all data from it.  The firmware "
              "is left unchanged."))
        wipe_device_msg.setWordWrap(True)
        wipe_device_warning = QLabel(
            _("Only wipe a device if you have the recovery seed written down "
              "and the device wallet(s) are empty, otherwise the bitcoins "
              "will be lost forever."))
        wipe_device_warning.setWordWrap(True)
        wipe_device_warning.setStyleSheet("color: red")
        advanced_glayout.addWidget(wipe_device_button, 6, 2)
        advanced_glayout.addWidget(wipe_device_msg, 7, 0, 1, 5)
        advanced_glayout.addWidget(wipe_device_warning, 8, 0, 1, 5)
        advanced_layout.addLayout(advanced_glayout)
        advanced_layout.addStretch(1)

        tabs = QTabWidget(self)
        tabs.addTab(info_tab, _("Information"))
        tabs.addTab(settings_tab, _("Settings"))
        tabs.addTab(advanced_tab, _("Advanced"))
        dialog_vbox = QVBoxLayout(self)
        dialog_vbox.addWidget(tabs)
        dialog_vbox.addLayout(Buttons(CloseButton(self)))

        # Update information
        invoke_client(None)


class WCSafeTInitMethod(WizardComponent):
    def __init__(self, parent, wizard):
        WizardComponent.__init__(self, parent, wizard, title=_('HW Setup'))

    def on_ready(self):
        _name, _info = self.wizard_data['hardware_device']
        msg = _("Choose how you want to initialize your {}.\n\n"
                "The first two methods are secure as no secret information "
                "is entered into your computer.\n\n"
                "For the last two methods you input secrets on your keyboard "
                "and upload them to your {}, and so you should "
                "only do those on a computer you know to be trustworthy "
                "and free of malware."
                ).format(_info.model_name, _info.model_name)
        choices = [
            # Must be short as QT doesn't word-wrap radio button text
            (TIM_NEW, _("Let the device generate a completely new seed randomly")),
            (TIM_RECOVER, _("Recover from a seed you have previously written down")),
            (TIM_MNEMONIC, _("Upload a BIP39 mnemonic to generate the seed")),
            (TIM_PRIVKEY, _("Upload a master private key"))
        ]
        self.choice_w = ChoiceWidget(message=msg, choices=choices)
        self.layout().addWidget(self.choice_w)
        self.layout().addStretch(1)

        self._valid = True

    def apply(self):
        self.wizard_data['safe_t_init'] = self.choice_w.selected_item[0]


class WCSafeTInitParams(WizardComponent):
    def __init__(self, parent, wizard):
        WizardComponent.__init__(self, parent, wizard, title=_('Set-up safe-t'))
        self.plugins = wizard.plugins
        self._busy = True

    def on_ready(self):
        _name, _info = self.wizard_data['hardware_device']
        self.settings_layout = SafeTInitLayout(self.wizard_data['safe_t_init'], _info.device.id_)
        self.settings_layout.validChanged.connect(self.on_settings_valid_changed)
        self.layout().addLayout(self.settings_layout)
        self.layout().addStretch(1)

        self.valid = self.wizard_data['safe_t_init'] != TIM_PRIVKEY
        self.busy = False

    def on_settings_valid_changed(self, is_valid: bool):
        self.valid = is_valid

    def apply(self):
        self.wizard_data['safe_t_settings'] = self.settings_layout.get_settings()


class WCSafeTInit(WizardComponent, Logger):
    def __init__(self, parent, wizard):
        WizardComponent.__init__(self, parent, wizard, title=_('Set-up safe-t'))
        Logger.__init__(self)
        self.plugins = wizard.plugins
        self.plugin = self.plugins.get_plugin('safe_t')

        self.layout().addWidget(WWLabel('Done'))

        self._busy = True

    def on_ready(self):
        settings = self.wizard_data['safe_t_settings']
        method = self.wizard_data['safe_t_init']
        _name, _info = self.wizard_data['hardware_device']
        device_id = _info.device.id_
        client = self.plugins.device_manager.client_by_id(device_id, scan_now=False)
        client.handler = self.plugin.create_handler(self.wizard)

        def initialize_device_task(settings, method, device_id, handler):
            try:
                self.plugin._initialize_device(settings, method, device_id, handler)
                self.logger.info('Done initialize device')
                self.valid = True
                self.wizard.requestNext.emit()  # triggers Next GUI thread from event loop
            except Exception as e:
                self.valid = False
                self.error = repr(e)
            finally:
                self.busy = False

        t = threading.Thread(
            target=initialize_device_task,
            args=(settings, method, device_id, client.handler),
            daemon=True)
        t.start()

    def apply(self):
        pass
