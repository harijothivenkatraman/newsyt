import ast, sys

files = [
    'pipeline.py',
    'article_ranker.py',
    'article_queue.py',
    'video/shorts_composer.py',
    'video/bundle_shorts_composer.py',
    'uploader/youtube_uploader.py',
    'content/ai_generator.py',
    'content/local_ml_generator.py',
    'dashboard/app.py',
]

errors = []
for f in files:
    try:
        ast.parse(open(f, encoding='utf-8').read())
        print(f'  OK: {f}')
    except SyntaxError as e:
        errors.append(f'  SYNTAX ERROR in {f} line {e.lineno}: {e.msg}')
        print(errors[-1])
    except FileNotFoundError:
        print(f'  SKIP (not found): {f}')

if errors:
    print(f'\n{len(errors)} error(s) found.')
    sys.exit(1)
else:
    print('\nAll files passed syntax check!')
