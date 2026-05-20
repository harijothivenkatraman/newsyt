with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The new HTML block ends with triple-quote followed by a newline then the
# old duplicate starts. Find the end of new HTML block (second triple-quote).
TQ = '"""'
first_start = content.index(TQ)          # HTML = """
first_end   = content.index(TQ, first_start + 3) + 3  # closing """

# Find routes section
routes_idx = content.index('# \u2500\u2500 Routes', first_end)

# Delete everything between the first HTML closing """ and "# Routes"
new_content = content[:first_end] + '\n\n' + content[routes_idx:]

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print(f'Deleted {routes_idx - first_end} chars of duplicate HTML.')
print(f'New file length: {len(new_content)} chars')
