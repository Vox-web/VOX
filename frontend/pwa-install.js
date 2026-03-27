(function () {
    const PWA_I18N = {
        uk: {
            installLabel: 'Додати на головний екран',
            installedLabel: 'Застосунок встановлено',
            openBrowserLabel: 'Відкрити в браузері для встановлення',
            howToInstallLabel: 'Як встановити застосунок',

            modalTitleInstall: 'Встановити VOX',
            modalTextInstall: 'Додайте VOX на головний екран для швидкого доступу.',
            modalTitleInstalled: 'VOX вже встановлено',
            modalTextInstalled: 'Застосунок уже додано на головний екран.',

            actionInstall: 'Встановити',
            actionClose: 'Закрити',
            actionDone: 'Готово',
            actionGotIt: 'Зрозуміло',

            helpIos: 'На iPhone/iPad стандартне вікно встановлення не з’являється.',
            helpTelegram: 'У Telegram вбудований браузер часто блокує нормальне встановлення PWA.',

            stepPrompt1: 'Натисніть <b>«Встановити»</b>.',
            stepPrompt2: 'Підтвердіть встановлення у вікні браузера.',
            stepPrompt3: 'Після цього VOX з’явиться на головному екрані.',

            stepIos1: 'Відкрийте меню <b>Поділитися</b> у Safari.',
            stepIos2: 'Оберіть <b>«На екран “Початок”»</b>.',
            stepIos3: 'Підтвердіть додавання.',

            stepTelegram1: 'Відкрийте VOX через <b>Chrome</b> або <b>Safari</b>, а не всередині Telegram.',
            stepTelegram2: 'Після цього знову відкрийте сторінку VOX у звичайному браузері.',
            stepTelegram3: 'Далі натисніть кнопку встановлення або скористайтеся меню браузера.',

            stepFallback1: 'Відкрийте меню браузера.',
            stepFallback2: 'Оберіть <b>«Встановити застосунок»</b> або <b>«Додати на головний екран»</b>.',
            stepFallback3: 'Підтвердіть дію.'
        },

        en: {
            installLabel: 'Add to Home Screen',
            installedLabel: 'App installed',
            openBrowserLabel: 'Open in browser to install',
            howToInstallLabel: 'How to install app',

            modalTitleInstall: 'Install VOX',
            modalTextInstall: 'Add VOX to your home screen for faster access.',
            modalTitleInstalled: 'VOX is already installed',
            modalTextInstalled: 'The app has already been added to your home screen.',

            actionInstall: 'Install',
            actionClose: 'Close',
            actionDone: 'Done',
            actionGotIt: 'Got it',

            helpIos: 'On iPhone/iPad there is no standard install prompt.',
            helpTelegram: 'Telegram in-app browser often blocks normal PWA installation.',

            stepPrompt1: 'Tap <b>Install</b>.',
            stepPrompt2: 'Confirm installation in the browser prompt.',
            stepPrompt3: 'VOX will then appear on your home screen.',

            stepIos1: 'Open the <b>Share</b> menu in Safari.',
            stepIos2: 'Choose <b>Add to Home Screen</b>.',
            stepIos3: 'Confirm the action.',

            stepTelegram1: 'Open VOX in <b>Chrome</b> or <b>Safari</b>, not inside Telegram.',
            stepTelegram2: 'Then open the VOX page again in the regular browser.',
            stepTelegram3: 'After that tap the install button or use the browser menu.',

            stepFallback1: 'Open the browser menu.',
            stepFallback2: 'Choose <b>Install app</b> or <b>Add to Home Screen</b>.',
            stepFallback3: 'Confirm the action.'
        },

        de: {
            installLabel: 'Zum Startbildschirm hinzufügen',
            installedLabel: 'App installiert',
            openBrowserLabel: 'Im Browser öffnen, um zu installieren',
            howToInstallLabel: 'So installieren Sie die App',

            modalTitleInstall: 'VOX installieren',
            modalTextInstall: 'Fügen Sie VOX zum Startbildschirm hinzu, um schneller darauf zuzugreifen.',
            modalTitleInstalled: 'VOX ist bereits installiert',
            modalTextInstalled: 'Die App wurde bereits zum Startbildschirm hinzugefügt.',

            actionInstall: 'Installieren',
            actionClose: 'Schließen',
            actionDone: 'Fertig',
            actionGotIt: 'Verstanden',

            helpIos: 'Auf iPhone/iPad gibt es kein Standard-Installationsfenster.',
            helpTelegram: 'Der integrierte Telegram-Browser blockiert die normale PWA-Installation oft.',

            stepPrompt1: 'Tippen Sie auf <b>„Installieren“</b>.',
            stepPrompt2: 'Bestätigen Sie die Installation im Browserfenster.',
            stepPrompt3: 'Danach erscheint VOX auf Ihrem Startbildschirm.',

            stepIos1: 'Öffnen Sie im Safari-Browser das Menü <b>Teilen</b>.',
            stepIos2: 'Wählen Sie <b>„Zum Home-Bildschirm“</b>.',
            stepIos3: 'Bestätigen Sie das Hinzufügen.',

            stepTelegram1: 'Öffnen Sie VOX in <b>Chrome</b> oder <b>Safari</b>, nicht im Telegram-internen Browser.',
            stepTelegram2: 'Öffnen Sie danach die VOX-Seite erneut im normalen Browser.',
            stepTelegram3: 'Tippen Sie dann auf die Installationsschaltfläche oder nutzen Sie das Browser-Menü.',

            stepFallback1: 'Öffnen Sie das Browser-Menü.',
            stepFallback2: 'Wählen Sie <b>„App installieren“</b> oder <b>„Zum Startbildschirm hinzufügen“</b>.',
            stepFallback3: 'Bestätigen Sie den Vorgang.'
        }
    };

    function getUILang() {
        const lang = (window.uiLang || localStorage.getItem('vox_ui_lang') || 'uk').toLowerCase();
        if (lang === 'uk' || lang === 'en' || lang === 'de') return lang;
        return 'en';
    }

    function tr(key) {
        const lang = getUILang();
        return (PWA_I18N[lang] && PWA_I18N[lang][key]) || PWA_I18N.en[key] || key;
    }

    function isStandaloneMode() {
        return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    }

    function isIOS() {
        return /iphone|ipad|ipod/i.test(navigator.userAgent);
    }

    function isTelegramWebView() {
        const ua = navigator.userAgent || '';
        return /telegram/i.test(ua) || !!(window.Telegram && window.Telegram.WebApp);
    }

    let deferredPrompt = null;
    let pwaBtn = null;
    let pwaLabel = null;
    let pwaModal = null;
    let pwaModalTitle = null;
    let pwaModalText = null;
    let pwaSteps = null;
    let pwaHelpNote = null;
    let pwaModalActionBtn = null;
    let pwaModalCloseBtn = null;

    function setButtonLabel(text) {
        if (pwaLabel) pwaLabel.innerHTML = text;
    }

    function setButtonState() {
        if (!pwaBtn) return;

        pwaBtn.style.display = 'flex';
        pwaBtn.classList.remove('installed');

        if (isStandaloneMode()) {
            pwaBtn.classList.add('installed');
            setButtonLabel(tr('installedLabel'));
            return;
        }

        if (isTelegramWebView()) {
            setButtonLabel(tr('openBrowserLabel'));
            return;
        }

        if (isIOS()) {
            setButtonLabel(tr('installLabel'));
            return;
        }

        if (deferredPrompt) {
            setButtonLabel(tr('installLabel'));
            return;
        }

        setButtonLabel(tr('howToInstallLabel'));
    }

    function fillSteps(items) {
        if (!pwaSteps) return;
        pwaSteps.innerHTML = items.map(s => `<div class="pwa-step">${s}</div>`).join('');
    }

    function openModal() {
        if (!pwaModal) return;

        if (isStandaloneMode()) {
            pwaModalTitle.textContent = tr('modalTitleInstalled');
            pwaModalText.textContent = tr('modalTextInstalled');
            fillSteps([]);
            pwaHelpNote.textContent = '';
            pwaModalActionBtn.style.display = 'none';
            pwaModalCloseBtn.textContent = tr('actionDone');
            pwaModal.classList.add('open');
            return;
        }

        pwaModalTitle.textContent = tr('modalTitleInstall');
        pwaModalText.textContent = tr('modalTextInstall');
        pwaModalCloseBtn.textContent = tr('actionClose');

        if (isTelegramWebView()) {
            fillSteps([tr('stepTelegram1'), tr('stepTelegram2'), tr('stepTelegram3')]);
            pwaHelpNote.textContent = tr('helpTelegram');
            pwaModalActionBtn.style.display = 'none';
            pwaModal.classList.add('open');
            return;
        }

        if (isIOS()) {
            fillSteps([tr('stepIos1'), tr('stepIos2'), tr('stepIos3')]);
            pwaHelpNote.textContent = tr('helpIos');
            pwaModalActionBtn.style.display = 'none';
            pwaModal.classList.add('open');
            return;
        }

        if (deferredPrompt) {
            fillSteps([tr('stepPrompt1'), tr('stepPrompt2'), tr('stepPrompt3')]);
            pwaHelpNote.textContent = '';
            pwaModalActionBtn.style.display = 'block';
            pwaModalActionBtn.textContent = tr('actionInstall');
            pwaModal.classList.add('open');
            return;
        }

        fillSteps([tr('stepFallback1'), tr('stepFallback2'), tr('stepFallback3')]);
        pwaHelpNote.textContent = '';
        pwaModalActionBtn.style.display = 'none';
        pwaModal.classList.add('open');
    }

    function closeModal() {
        if (pwaModal) pwaModal.classList.remove('open');
    }

    async function handleInstall() {
        if (isStandaloneMode()) {
            closeModal();
            return;
        }

        if (deferredPrompt) {
            try {
                deferredPrompt.prompt();
                await deferredPrompt.userChoice;
            } catch (_) {
            } finally {
                deferredPrompt = null;
                closeModal();
                setButtonState();
            }
            return;
        }

        closeModal();
    }

    function injectModal() {
        if (document.getElementById('pwaModal')) return;

        const wrap = document.createElement('div');
        wrap.innerHTML = `
            <div class="pwa-modal-overlay" id="pwaModal">
                <div class="pwa-modal">
                    <div class="pwa-modal-title" id="pwaModalTitle"></div>
                    <div class="pwa-modal-text" id="pwaModalText"></div>
                    <div class="pwa-steps" id="pwaSteps"></div>
                    <div class="pwa-help-note" id="pwaHelpNote"></div>
                    <div class="pwa-modal-actions">
                        <button class="pwa-modal-btn secondary" type="button" id="pwaModalCloseBtn"></button>
                        <button class="pwa-modal-btn primary" type="button" id="pwaModalActionBtn"></button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(wrap.firstElementChild);

        pwaModal = document.getElementById('pwaModal');
        pwaModalTitle = document.getElementById('pwaModalTitle');
        pwaModalText = document.getElementById('pwaModalText');
        pwaSteps = document.getElementById('pwaSteps');
        pwaHelpNote = document.getElementById('pwaHelpNote');
        pwaModalActionBtn = document.getElementById('pwaModalActionBtn');
        pwaModalCloseBtn = document.getElementById('pwaModalCloseBtn');

        pwaModalCloseBtn.addEventListener('click', closeModal);
        pwaModalActionBtn.addEventListener('click', handleInstall);
        pwaModal.addEventListener('click', (e) => {
            if (e.target === pwaModal) closeModal();
        });
    }

    function initPwaInstall() {
        pwaBtn = document.getElementById('pwaInstallBtn');
        pwaLabel = document.getElementById('pwaInstallLabel');

        if (!pwaBtn || !pwaLabel) return;

        injectModal();

        pwaBtn.removeAttribute('onclick');
        pwaBtn.addEventListener('click', openModal);

        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js').catch(() => {});
        }

        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
            setButtonState();
        });

        window.addEventListener('appinstalled', () => {
            deferredPrompt = null;
            closeModal();
            setButtonState();
        });

        window.addEventListener('focus', setButtonState);
        window.addEventListener('pageshow', setButtonState);

        setButtonState();
    }

    window.initPwaInstall = initPwaInstall;
    window.refreshPwaInstallUI = setButtonState;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPwaInstall);
    } else {
        initPwaInstall();
    }
})();