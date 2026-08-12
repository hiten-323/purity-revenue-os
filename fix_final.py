path = r'C:\Users\hiten\Desktop\ppp\claude\CODE\purity_beans_ai\jules_session\frontend\src\app\dashboard\page.tsx'
with open(path, encoding='utf-8') as f:
    content = f.read()

fixes = [
    (' (${lead.email}) ?`, "ok");', ' (${lead.email})`, "ok");'),
    ('>All sent ?</span>', '>All sent ✓</span>'),
    ('"Connecting · "', '"Connecting..."'),
]
for old, new in fixes:
    n = content.count(old)
    print(f'{n}x fixed')
    content = content.replace(old, new)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
