import sys
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

path = r'C:\Users\hiten\Desktop\ppp\claude\CODE\purity_beans_ai\jules_session\frontend\src\app\dashboard\page.tsx'
with open(path, encoding='utf-8') as f:
    content = f.read()

fixes = [
    # Currency Rs. prefix in fmt() function
    ('`?${(v / 10_000_000).toFixed(1)}Cr`', '`Rs.${(v / 10_000_000).toFixed(1)}Cr`'),
    ('`?${(v / 100_000).toFixed(1)}L`',      '`Rs.${(v / 100_000).toFixed(1)}L`'),
    ('`?${v.toLocaleString("en-IN")}`',       '`Rs.${v.toLocaleString("en-IN")}`'),

    # Rating star
    ('"4.3? avg', '"4.3★ avg'),

    # Trailing success toasts - remove trailing ?
    ('(${lead.email}) ?", "ok")', '(${lead.email})", "ok")'),
    ('connect@purepantryprovisions.com) ?", "ok")', 'connect@purepantryprovisions.com)", "ok")'),

    # Checkmark icons in JSX spans
    ('text-emerald-400">?</span>', 'text-emerald-400">✓</span>'),

    # Review & Send button trailing ?
    ('Review & Send ?', 'Review & Send'),

    # Close / delete buttons
    ('>?</button>', '>×</button>'),

    # "? Sent" label
    ('font-black pt-0.5">? Sent</span>', 'font-black pt-0.5">✓ Sent</span>'),

    # "? Personalized First Line" — literal ? (not FFFD)
    ('? Personalized First Line', 'Personalized First Line'),

    # Send to All button prefix
    ('`? Send to All ${distList.length} Distributors`', '`Send to All ${distList.length} Distributors`'),

    # All sent trailing
    ('"All sent ?"', '"All sent ✓"'),

    # Empty state icon
    ('text-2xl mb-2">?</p>', 'text-2xl mb-2">☑</p>'),

    # Tender review steps separator
    ('Review ? Approve ? Auto-send', 'Review · Approve · Auto-send'),

    # Campaign button leftover from FFFD fix
    ('Sending...Campaign · ', 'Sending... '),

    # Discovery toast prefix
    ('`? ${json.total_new_leads} new leads', '`+ ${json.total_new_leads} new leads'),

    # Success icon in discovery result
    ('<span className="text-emerald-400">?</span>', '<span className="text-emerald-400">✓</span>'),
]

count = 0
for old, new in fixes:
    n = content.count(old)
    if n:
        content = content.replace(old, new)
        print(f'[{n}x] fixed')
        count += n

print(f'Total: {count} fixes')
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Saved.')
