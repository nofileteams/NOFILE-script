import sys
import os
import re
import time
import shutil
import random as _random
import subprocess
import zipfile

try:
    import msvcrt
except ImportError:
    msvcrt = None

VARS = {}
LABELS = {}

VAR_PATTERN = re.compile(r'\$([A-Za-z0-9_]+)\$')


class Goto(Exception):
    def __init__(self, label):
        super().__init__(label)
        self.label = label


def sub_vars(text):
    def repl(m):
        return VARS.get(m.group(1), '')
    return VAR_PATTERN.sub(repl, text)


def strip_quotes(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def resolve(raw):
    return sub_vars(strip_quotes(raw.strip()))


def resolve_lhs(raw):
    raw = raw.strip()
    mtrim = re.match(r'^trim\((.*)\)$', raw)
    if mtrim:
        return resolve_lhs(mtrim.group(1)).strip()
    val = resolve(raw)
    if val and os.path.isfile(val):
        try:
            with open(val, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read().strip()
        except Exception:
            return val
    return val


def split_candidates(raw):
    out = []
    for p in re.split(r'[,&]', raw):
        out.append(sub_vars(strip_quotes(p.strip())))
    return out


def exact_match(lhs_raw, rhs_raw):
    lhs_val = resolve_lhs(lhs_raw)
    return lhs_val in split_candidates(rhs_raw)


def partial_match(lhs_raw, rhs_raw):
    lhs_val = resolve_lhs(lhs_raw)
    for c in split_candidates(rhs_raw):
        if c != '' and c in lhs_val:
            return True
    return False


def numeric_cmp(lhs_raw, rhs_raw, op):
    lhs_val = resolve_lhs(lhs_raw)
    rhs_val = sub_vars(strip_quotes(rhs_raw.strip()))
    try:
        lv = float(lhs_val)
        rv = float(rhs_val)
    except Exception:
        return False
    if op == '<=':
        return lv <= rv
    return lv >= rv


def evaluate_condition(raw_cond):
    raw_cond = raw_cond.strip()
    if '<=' in raw_cond:
        lhs, rhs = raw_cond.split('<=', 1)
        return numeric_cmp(lhs, rhs, '<=')
    if '>=' in raw_cond:
        lhs, rhs = raw_cond.split('>=', 1)
        return numeric_cmp(lhs, rhs, '>=')
    if '==' in raw_cond:
        lhs, rhs = raw_cond.split('==', 1)
        return partial_match(lhs, rhs)
    if '=' in raw_cond:
        lhs, rhs = raw_cond.split('=', 1)
        return exact_match(lhs, rhs)
    return False


def evaluate_count(text):
    val = sub_vars(text.strip())
    try:
        return int(float(val))
    except Exception:
        return 0


def split_redirect(line):
    m = re.match(r'^(.*?)\s*>\s*(.+)$', line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return line.strip(), None


def write_result(text, target):
    if not target:
        print(text)
        return
    mfile = re.match(r'^file\(\s*(.*?)\s*\)$', target)
    if mfile:
        path = sub_vars(strip_quotes(mfile.group(1)))
        try:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
        except Exception:
            pass
        return
    mvar = re.match(r'^(\w+)\.val$', target)
    if mvar:
        VARS[mvar.group(1)] = text
        return
    print(text)


def find_block(lines, header_idx, hi):
    depth = 1
    i = header_idx + 1
    else_idx = None
    while i <= hi:
        raw = lines[i].strip()
        if re.match(r'^\}\s*else\s*\{$', raw) and depth == 1:
            else_idx = i
        depth += raw.count('{') - raw.count('}')
        if depth == 0:
            return i, else_idx
        i += 1
    return hi, else_idx


def handle_get_loc(left, target):
    m = re.match(r'^get\.loc\s*=\s*(.+)$', left)
    path = resolve(m.group(1))
    result = "true" if path and (os.path.isdir(path) or os.path.isfile(path)) else "false"
    write_result(result, target)


def handle_get_file(left, target):
    m = re.match(r'^get\.file\s*=\s*(.+)$', left)
    rhs = m.group(1).strip()
    mline = re.match(r'^(.+?)&(.+)$', rhs)
    line_num = None
    invalid_line = False
    if mline:
        path = resolve(mline.group(1))
        line_spec = sub_vars(mline.group(2).strip())
        try:
            line_num = int(float(line_spec))
        except Exception:
            invalid_line = True
    else:
        path = resolve(rhs)
    if invalid_line:
        content = "error"
    elif path and os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                if line_num is not None:
                    file_lines = f.read().splitlines()
                    if 1 <= line_num <= len(file_lines):
                        content = file_lines[line_num - 1]
                    else:
                        content = "error"
                else:
                    content = f.read()
        except Exception:
            content = "error"
    else:
        content = "error"
    write_result(content, target)


def handle_log(left, target):
    m = re.match(r'^log\((.*)\)$', left)
    raw = m.group(1).strip()
    raw_sub = sub_vars(raw)
    if len(raw_sub) >= 2 and raw_sub[0] == '"' and raw_sub[-1] == '"':
        text = raw_sub[1:-1]
    else:
        try:
            value = eval(raw_sub, {'__builtins__': {}}, {})
            text = str(value)
        except Exception:
            text = strip_quotes(raw_sub)
    write_result(text, target)


def handle_del(left, target):
    m = re.match(r'^del\((.*)\)$', left)
    path = resolve(m.group(1))
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            result = "true"
        elif os.path.isfile(path):
            os.remove(path)
            result = "true"
        else:
            result = "false"
    except Exception:
        result = "error"
    if target:
        write_result(result, target)


def handle_mov(left, target):
    m = re.match(r'^mov\s*\(\s*(.+?)\s+to\s+(.+?)\s*\)$', left)
    src = resolve(m.group(1))
    dst = resolve(m.group(2))
    try:
        shutil.move(src, dst)
        result = "true"
    except Exception:
        result = "error"
    if target:
        write_result(result, target)


def handle_create(left, target):
    m = re.match(r'^create\((.*)\)$', left)
    path = resolve(m.group(1))
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, 'a', encoding='utf-8'):
            pass
        result = "true"
    except Exception:
        result = "error"
    if target:
        write_result(result, target)


def handle_sleep(left):
    m = re.match(r'^time\.sleep\((.*)\)$', left)
    val = sub_vars(m.group(1).strip())
    try:
        time.sleep(float(val))
    except Exception:
        pass


def handle_random(left, target):
    m = re.match(r'^random\s*=\s*(.+)$', left)
    parts = m.group(1).split(',')
    low_raw = sub_vars(parts[0].strip())
    high_raw = sub_vars(parts[1].strip()) if len(parts) > 1 else low_raw
    try:
        if '.' in low_raw or '.' in high_raw:
            value = _random.uniform(float(low_raw), float(high_raw))
            text = str(round(value, 4))
        else:
            value = _random.randint(int(low_raw), int(high_raw))
            text = str(value)
    except Exception:
        text = "error"
    write_result(text, target)


def handle_copy(left, target):
    m = re.match(r'^copy\(\s*(.+?)\s+to\s+(.+?)\s*\)$', left)
    src = resolve(m.group(1))
    dst = resolve(m.group(2))
    try:
        if os.path.isdir(src):
            dst_final = dst if not os.path.isdir(dst) else os.path.join(dst, os.path.basename(src))
            shutil.copytree(src, dst_final)
        else:
            d = os.path.dirname(dst)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            shutil.copy2(src, dst)
        result = "true"
    except Exception:
        result = "error"
    if target:
        write_result(result, target)


def handle_unzip(left, target):
    m = re.match(r'^unzip\((.*)\)$', left)
    path = resolve(m.group(1))
    try:
        dest = os.path.dirname(path) or '.'
        with zipfile.ZipFile(path, 'r') as zf:
            zf.extractall(dest)
        result = "true"
    except Exception:
        result = "error"
    if target:
        write_result(result, target)


def dispatch_cmd_for_path(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.py':
        return [sys.executable, path]
    if ext in ('.bat', '.cmd'):
        return ['cmd', '/c', path]
    if ext == '.ps1':
        return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path]
    return [path]


def handle_start_file(left, target):
    m = re.match(r'^start\.file\((.*)\)$', left)
    path = resolve(m.group(1))
    cmd = dispatch_cmd_for_path(path)
    try:
        if target:
            completed = subprocess.run(cmd, capture_output=True, text=True)
            write_result(completed.stdout.rstrip('\n'), target)
        else:
            subprocess.run(cmd)
    except Exception:
        if target:
            write_result("error", target)
        else:
            print("error")


def handle_start_cmd(left, target):
    m = re.match(r'^start\.cmd\((.*)\)$', left)
    cmdtext = sub_vars(strip_quotes(m.group(1).strip()))
    try:
        if target:
            completed = subprocess.run(['cmd', '/c', cmdtext], capture_output=True, text=True)
            write_result(completed.stdout.rstrip('\n'), target)
        else:
            subprocess.run(['cmd', '/c', cmdtext])
    except Exception:
        if target:
            write_result("error", target)
        else:
            print("error")


def handle_start_pwsh(left, target):
    m = re.match(r'^start\.pwsh\((.*)\)$', left)
    cmdtext = sub_vars(strip_quotes(m.group(1).strip()))
    try:
        if target:
            completed = subprocess.run(['powershell', '-NoProfile', '-Command', cmdtext], capture_output=True, text=True)
            write_result(completed.stdout.rstrip('\n'), target)
        else:
            subprocess.run(['powershell', '-NoProfile', '-Command', cmdtext])
    except Exception:
        if target:
            write_result("error", target)
        else:
            print("error")


def handle_input(left, target):
    m = re.match(r'^input\((.*)\)$', left)
    prompt = sub_vars(strip_quotes(m.group(1).strip()))
    value = input(prompt)
    if target:
        write_result(value, target)


def handle_pause():
    print("続行するには何かキーを押してください . . . ", end='', flush=True)
    if msvcrt:
        msvcrt.getch()
    else:
        input()
    print()


def handle_addon(left, target):
    m = re.match(r'^(\w+)((?:[.,]\w+(?:\([^)]*\))?)+)$', left)
    if not m:
        if target:
            write_result("error", target)
        else:
            print("error")
        return
    name = m.group(1)
    rest = m.group(2)
    parts = re.findall(r'([.,])(\w+)(\([^)]*\))?', rest)
    args = []
    for sep, ident, argparen in parts:
        args.append(('-' if sep == ',' else '--') + ident)
        if argparen:
            inner = argparen[1:-1]
            args.append(sub_vars(strip_quotes(inner.strip())))
    addon_dir = r'C:\Users\Public\Documents\addon'
    py_path = os.path.join(addon_dir, name + '.py')
    exe_path = os.path.join(addon_dir, name + '.exe')
    if os.path.isfile(py_path):
        cmd = [sys.executable, py_path] + args
    elif os.path.isfile(exe_path):
        cmd = [exe_path] + args
    else:
        if target:
            write_result("error", target)
        else:
            print("error")
        return
    try:
        if target:
            completed = subprocess.run(cmd, capture_output=True, text=True)
            write_result(completed.stdout.rstrip('\n'), target)
        else:
            subprocess.run(cmd)
    except Exception:
        if target:
            write_result("error", target)
        else:
            print("error")


def execute_statement(line):
    left, target = split_redirect(line)
    if re.match(r'^get\.loc\s*=', left):
        handle_get_loc(left, target)
    elif re.match(r'^get\.file\s*=', left):
        handle_get_file(left, target)
    elif left.startswith('log('):
        handle_log(left, target)
    elif left.startswith('del('):
        handle_del(left, target)
    elif re.match(r'^mov\s*\(', left):
        handle_mov(left, target)
    elif left.startswith('create('):
        handle_create(left, target)
    elif left == 'exit':
        sys.exit(0)
    elif re.match(r'^time\.sleep\(', left):
        handle_sleep(left)
    elif re.match(r'^random\s*=', left):
        handle_random(left, target)
    elif re.match(r'^copy\s*\(', left):
        handle_copy(left, target)
    elif re.match(r'^unzip\(', left):
        handle_unzip(left, target)
    elif re.match(r'^start\.file\(', left):
        handle_start_file(left, target)
    elif re.match(r'^start\.cmd\(', left):
        handle_start_cmd(left, target)
    elif re.match(r'^start\.pwsh\(', left):
        handle_start_pwsh(left, target)
    elif left.startswith('input('):
        handle_input(left, target)
    elif left == 'pause':
        handle_pause()
    elif left in ('clear', 'cls'):
        os.system('cls')
    else:
        handle_addon(left, target)


def run(lines, lo, hi):
    i = lo
    while i <= hi:
        line = lines[i].strip()
        if line == '' or re.match(r'^:\w+$', line) or line == '}' or re.match(r'^\}\s*else\s*\{$', line):
            i += 1
            continue
        mif = re.match(r'^if\s*\((.*)\)\s*\{$', line)
        if mif:
            cond_text = mif.group(1)
            close_idx, else_idx = find_block(lines, i, hi)
            if_body_start = i + 1
            if_body_end = (else_idx - 1) if else_idx is not None else (close_idx - 1)
            after = close_idx + 1
            try:
                if evaluate_condition(cond_text):
                    if if_body_start <= if_body_end:
                        run(lines, if_body_start, if_body_end)
                elif else_idx is not None:
                    else_body_start = else_idx + 1
                    else_body_end = close_idx - 1
                    if else_body_start <= else_body_end:
                        run(lines, else_body_start, else_body_end)
            except Goto as g:
                target = LABELS.get(g.label)
                if target is not None and lo <= target <= hi:
                    i = target
                    continue
                raise
            i = after
            continue
        mcount = re.match(r'^count\s*\((.*)\)\s*\{$', line)
        if mcount:
            count_text = mcount.group(1)
            close_idx, _ = find_block(lines, i, hi)
            body_start = i + 1
            body_end = close_idx - 1
            after = close_idx + 1
            n = evaluate_count(count_text)
            try:
                k = 0
                while k < n:
                    if body_start <= body_end:
                        run(lines, body_start, body_end)
                    k += 1
            except Goto as g:
                target = LABELS.get(g.label)
                if target is not None and lo <= target <= hi:
                    i = target
                    continue
                raise
            i = after
            continue
        mgoto = re.match(r'^goto\s+(\w+)$', line)
        if mgoto:
            label = mgoto.group(1)
            target = LABELS.get(label)
            if target is not None and lo <= target <= hi:
                i = target
                continue
            raise Goto(label)
        execute_statement(line)
        i += 1


def build_labels(lines):
    labels = {}
    for idx, l in enumerate(lines):
        m = re.match(r'^:(\w+)$', l.strip())
        if m:
            labels[m.group(1)] = idx
    return labels


def run_program(lines):
    global LABELS
    LABELS = build_labels(lines)
    try:
        run(lines, 0, len(lines) - 1)
    except Goto:
        pass


def repl():
    while True:
        try:
            line = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() == '':
            continue
        try:
            run_program([line])
        except SystemExit:
            break
        except Exception:
            print("error")


def main():
    if len(sys.argv) < 2:
        repl()
        return
    nfile_path = sys.argv[1]
    if not os.path.isfile(nfile_path):
        print("error")
        return
    with open(nfile_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.read().splitlines()
    try:
        run_program(lines)
    except SystemExit:
        pass
    except Exception:
        print("error")


if __name__ == '__main__':
    main()
