import json
import re
with open('flash_hunter.log', 'r') as f:
    for line in f:
        if '"edges"' in line:
            match = re.search(r'"edges\":\s*(\d+)', line)
            if match:
                edges = int(match.group(1))
                if edges > 0:
                    print('Found edges >0:', line.strip())
                    break
    else:
        print('No edges >0 found in log')