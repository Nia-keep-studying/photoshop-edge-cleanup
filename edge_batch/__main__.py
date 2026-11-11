from __future__ import annotations
import argparse,json,threading
from .common import defaults,read
from .importer import create_batch
from .pipeline import Pipeline,retry_exceptions
from .store import Store

def main():
    parser=argparse.ArgumentParser(description='参考图驱动的 Photoshop 批量去边工作流')
    sub=parser.add_subparsers(dest='command')
    gui=sub.add_parser('gui');gui.add_argument('--settings')
    add=sub.add_parser('import');add.add_argument('--input',required=True);add.add_argument('--references');add.add_argument('--output',required=True);add.add_argument('--settings')
    for name in ('run','status','retry'):
        p=sub.add_parser(name);p.add_argument('--output',required=True);p.add_argument('--batch',required=True)
    args=parser.parse_args()
    if args.command in (None,'gui'):
        from .gui import launch
        launch(getattr(args,'settings',None));return
    if args.command=='import':
        store,bid,created=create_batch(args.input,args.references,args.output,read(args.settings) if args.settings else defaults())
        print(json.dumps({'batch':bid,'created':created},ensure_ascii=False));return
    store=Store(args.output)
    if args.command=='status':print(json.dumps({'batch':store.batch(args.batch),'jobs':store.jobs(args.batch)},ensure_ascii=False,indent=2))
    elif args.command=='retry':retry_exceptions(store,args.batch)
    elif args.command=='run':
        stop=threading.Event();pipeline=Pipeline(store,args.batch,stop)
        try:pipeline.run()
        except KeyboardInterrupt:
            print('进程已中断；下次继续会核对 PS 指令记录。');raise
        print(json.dumps({'batch':args.batch,'state':store.batch(args.batch)['state'],'summary':str(pipeline.folder/'summary.json')},ensure_ascii=False))

if __name__=='__main__':main()
