#!/usr/bin/env python3
with open('app.py', encoding='utf-8') as f:
    lines = f.readlines()
    print(f'Total lines: {len(lines)}')
    last_section = [l.strip() for l in lines if 'with st.expander' in l][-1]
    print(f'Last section: {last_section[:80]}')
    
    # Check if 1.10 code is present
    section_1_10_line = next((i for i, l in enumerate(lines) if '1.10 Plan vs. Actuals' in l), None)
    if section_1_10_line:
        print(f'Section 1.10 found at line {section_1_10_line + 1}')
    else:
        print('ERROR: Section 1.10 not found!')
