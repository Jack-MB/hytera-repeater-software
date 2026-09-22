import re

def main():
    with open('mission_logger.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    start_idx = content.find('def _build_html(data, ids_map, mission_dir):')
    if start_idx != -1:
        content = content[:start_idx]
        with open('mission_logger.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print("Removed _build_html")
    else:
        print("Not found")

if __name__ == '__main__':
    main()
