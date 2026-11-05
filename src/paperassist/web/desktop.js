export function installDesktopBridge({ window, prepareClose, onError }) {
    let pending = null, approved = false;
    window.paperassistDesktop = {
        requestClose() {
            // Return the Promise to evaluate_js's callback; destroying inside an RPC
            // would leave pywebview trying to deliver its result to a closed window.
            pending ??= Promise.resolve().then(prepareClose).then((result) => {
                approved = !!result;
                return approved;
            }).catch((error) => {
                onError(error);
                return false;
            }).finally(() => { pending = null; });
            return pending;
        },
    };
    return { allowsUnload: () => approved };
}
