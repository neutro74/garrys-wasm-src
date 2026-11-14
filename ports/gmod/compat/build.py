#!/usr/bin/env python3
"""Build portable GLua interpreter. Upstream source stays untouched."""
from pathlib import Path
import argparse, os, shutil, subprocess

here=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--native',action='store_true')
args=parser.parse_args()
out=here/'build'
out.mkdir(exist_ok=True)
src=out/'lua'
shutil.copytree(here/'vendor/lua',src,dirs_exist_ok=True)

def edit(name,old,new):
    path=src/name
    text=path.read_text()
    assert text.count(old)==1, (name,old)
    path.write_text(text.replace(old,new))

# Lexer-level changes preserve strings, long strings, comments and source lines.
edit('llex.c','"and", "break", "do",','"and", "break", "continue", "do",')
edit('llex.h','TK_AND = FIRST_RESERVED, TK_BREAK,','TK_AND = FIRST_RESERVED, TK_BREAK, TK_CONTINUE,')
edit('llex.c',"      case '/': {\n        next(ls);\n        if (check_next1(ls, '/')) return TK_IDIV;  /* '//' */\n        else return '/';\n      }",r'''      case '!': {
        next(ls);
        if (check_next1(ls, '=')) return TK_NE;
        return TK_NOT;
      }
      case '&': {
        next(ls);
        if (check_next1(ls, '&')) return TK_AND;
        return '&';
      }
      case '|': {
        next(ls);
        if (check_next1(ls, '|')) return TK_OR;
        return '|';
      }
      case '/': {
        next(ls);
        if (check_next1(ls, '/')) {
          while (!currIsNewline(ls) && ls->current != EOZ) next(ls);
          break;
        }
        if (check_next1(ls, '*')) {
          for (;;) {
            if (ls->current == EOZ) lexerror(ls, "unfinished block comment", 0);
            if (currIsNewline(ls)) inclinenumber(ls);
            else if (ls->current == '*') {
              next(ls);
              if (check_next1(ls, '/')) break;
            }
            else next(ls);
          }
          break;
        }
        return '/';
      }''')
# Continue uses Lua's scoped goto machinery, with loop-specific tail labels.
# Inner loops resolve their pending jumps before outer loops see them.
edit('lparser.c','    case TK_BREAK: {  /* stat -> breakstat */',r'''    case TK_CONTINUE: {
      luaX_next(ls);
      newgotoentry(ls, luaS_newliteral(ls->L, "continue"), line, luaK_jump(ls->fs));
      break;
    }
    case TK_BREAK: {  /* stat -> breakstat */''')
edit('lparser.c','  block(ls);\n  luaK_jumpto(fs, whileinit);',
     '  block(ls);\n  createlabel(ls, luaS_newliteral(ls->L, "continue"), line, 0);\n  luaK_jumpto(fs, whileinit);')
edit('lparser.c','  condexit = cond(ls);  /* read condition (inside scope block) */',
     '  createlabel(ls, luaS_newliteral(ls->L, "continue"), line, 0);\n  condexit = cond(ls);  /* read condition (inside scope block) */')
edit('lparser.c','  leaveblock(fs);  /* end of scope for declared variables */',
     '  leaveblock(fs);  /* end of scope for declared variables */\n  createlabel(ls, luaS_newliteral(ls->L, "continue"), line, 0);')

sources=[str(p) for p in sorted(src.glob('*.c')) if p.name not in ('linit.c','liolib.c','loslib.c','loadlib.c')]
compiler=os.environ.get('EMCC',str(Path.home()/'.emsdk/upstream/emscripten/emcc'))
if args.native:
    cmd=['cc','-O2','-DGB_NATIVE','-I'+str(src),str(here/'runtime.c'),*sources,'-lm','-o',str(out/'gmod-lua')]
else:
    cmd=[compiler,'-O2','-I'+str(src),str(here/'runtime.c'),*sources,
         '-sMODULARIZE=1','-sEXPORT_ES6=1','-sENVIRONMENT=web,node',
         '-sALLOW_MEMORY_GROWTH=1','-sMAXIMUM_MEMORY=268435456','-sSTACK_SIZE=1048576','-sFORCE_FILESYSTEM=1',
         '-sEXPORTED_FUNCTIONS=["_malloc","_free","_gb_open","_gb_run","_gb_file","_gb_error","_gb_close","_gb_tick","_gb_compile"]',
         '-sEXPORTED_RUNTIME_METHODS=["ccall","FS","UTF8ToString","stringToUTF8"]',
         '-o',str(out/'gmod-lua.mjs')]
subprocess.run(cmd,check=True)
