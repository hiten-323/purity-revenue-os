
## 2026-10-23 22:xx IST - baseline opener text changed (founder-approved)
- Variant id unchanged: `baseline`. Allocation and e1_short / e2_stock_check / e3_hook / e4_micro (Vrini) unchanged.
- Cutover (UTC): see CUTOVER line below. Compare `baseline` call_results before vs after this time; do not pool.
- BEFORE (30 words): "Namaste, main Purity Beans ki taraf se AI assistant baat kar rahi hoon. Hum cafes aur businesses ko glass jar wali pure instant coffee provide karte hain — kya aapke paas ek chhota sa minute hai?"
- AFTER (16 words): "Namaste, Purity Beans ki AI assistant bol rahi hoon. Instant coffee ke baare mein ek minute milega?"
- Reason: nightly audit 23 Oct - ~3 of 4 answered calls hung up during the ~30-word greeting.
- CUTOVER: 2026-10-23T16:56:49Z (patched; live after purity-api/worker restart)
