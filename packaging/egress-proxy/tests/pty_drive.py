#!/usr/bin/env python3
"""像人一样在终端里回答提示（测试用）：
    pty_drive.py '提示片段=回答' ['提示片段=回答' ...] -- 命令 参数...
每个提示片段在子进程输出里出现之后，才把对应回答加回车敲进去。一次性把所有答案灌进 stdin 不行：
getpass 读密码前会 tcsetattr(TCSAFLUSH) 清掉输入队列，提前敲的密码会丢，进程就一直等在密码提示上。
子进程输出原样转到 stdout，退出码跟子进程；5 分钟没结束就杀掉、退出码 124。只依赖标准库，python 3.9 可用。"""
import os
import pty
import select
import sys
import time


def main():
    sep = sys.argv.index('--')
    steps = [arg.split('=', 1) for arg in sys.argv[1:sep]]
    cmd = sys.argv[sep + 1:]
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(cmd[0], cmd)
    buf = b''
    deadline = time.time() + 300
    timed_out = False
    while True:
        ready, _, _ = select.select([fd], [], [], 1.0)
        if ready:
            try:
                data = os.read(fd, 4096)
            except OSError:      # 子进程退出后 Linux 上读 master 报 EIO
                break
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.flush()
            buf += data
            if steps and steps[0][0].encode() in buf:
                time.sleep(0.2)  # 提示已经打出来，终端也已切好模式，再敲
                os.write(fd, (steps[0][1] + '\n').encode())
                buf = b''
                steps.pop(0)
        if time.time() > deadline:
            os.kill(pid, 9)
            timed_out = True
            break
    _, status = os.waitpid(pid, 0)
    if steps:
        print('pty_drive: 还有提示没等到: %s' % [s[0] for s in steps], file=sys.stderr)
    sys.exit(124 if timed_out else os.waitstatus_to_exitcode(status))


if __name__ == '__main__':
    main()
