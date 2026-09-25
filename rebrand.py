import os

def replace_in_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace text
    content = content.replace("Event Voting", "MEMECEPTION")
    
    # Replace the V and H logos with the Lucide package icon
    content = content.replace(">\n          V</div>", "><i data-lucide=\"package\" class=\"w-5 h-5\"></i></div>")
    content = content.replace(">V</div>", "><i data-lucide=\"package\" class=\"w-5 h-5\"></i></div>")
    
    content = content.replace(">H</div>", "><i data-lucide=\"package\" class=\"w-5 h-5\"></i></div>")
    
    # Increase the size for the login page specifically since it's a bit larger
    content = content.replace('text-xl mx-auto shadow-lg"><i data-lucide="package" class="w-5 h-5"></i>', 'text-xl mx-auto shadow-lg"><i data-lucide="package" class="w-7 h-7"></i>')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

for file in ["README.md", "app/main.py", "app/static/participant.html", "app/static/host.html", "app/static/host_login.html"]:
    if os.path.exists(file):
        replace_in_file(file)
        print(f"Rebranded {file}")
