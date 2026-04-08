function parseNum(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
}

function computeValuation(amount, equity) {
    if (amount > 0 && equity > 0) {
        return amount / (equity / 100);
    }
    return 0;
}

function formatMoney(value) {
    return "$" + Math.round(value).toLocaleString();
}

function wireValuation(amountId, equityId, targetId) {
    const amountInput = document.getElementById(amountId);
    const equityInput = document.getElementById(equityId);
    const target = document.getElementById(targetId);
    if (!amountInput || !equityInput || !target) {
        return;
    }
    const refresh = () => {
        const amount = parseNum(amountInput.value);
        const equity = parseNum(equityInput.value);
        const valuation = computeValuation(amount, equity);
        target.textContent = valuation > 0 ? formatMoney(valuation) : "--";
    };
    amountInput.addEventListener("input", refresh);
    equityInput.addEventListener("input", refresh);
    amountInput.addEventListener("change", refresh);
    equityInput.addEventListener("change", refresh);
    refresh();
}

document.addEventListener("DOMContentLoaded", () => {
    wireValuation("offer_amount", "offer_equity", "offer_valuation");
    wireValuation("ask_amount", "ask_equity", "ask_valuation");
});
