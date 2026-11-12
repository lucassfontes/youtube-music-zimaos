"""Offline tests of conflict detection and safe file replacement, no real YouTube calls."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import asynccontextmanager

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from test_app import main
import httpx

VID='dQw4w9WgXcQ'
URL='https://www.youtube.com/watch?v='+VID
HEADERS={'X-App-Request':'zimaos-music'}

class FakeDL:
    should_fail=False
    def __init__(self,params): self.params=params
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def extract_info(self,url,download=True):
        if self.should_fail: raise RuntimeError('falha simulada ao baixar')
        path=Path(self.params['outtmpl'].replace('%(id)s',VID).replace('%(ext)s','mp4'))
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'NOVO_VIDEO_COMPLETO')
        return {'id':VID}

class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        main.CONFIG=root/'dados';main.MUSIC=root/'Musica';main.DB=main.CONFIG/'app.db'
        main.JOBS=main.queue.Queue();main.CANCEL_EVENTS.clear()
        main.init_storage()
        self.root=main.video_root()
        self.old=self.root/'Meu Canal'/'Minha Faixa.mp4'
        self.old.parent.mkdir(parents=True,exist_ok=True)
    def completed(self,exists=True):
        if exists: self.old.write_bytes(b'ORIGINAL')
        with main.connect() as c:
            return c.execute("""INSERT INTO jobs(video_id,url,artist,track,album,media_type,status,path,created_at,updated_at)
                              VALUES(?,?,?,?,?,'video','concluido',?,?,?)""",
                             (VID,URL,'Meu Canal','Minha Faixa','', 'Meu Canal/Minha Faixa.mp4',main.now(),main.now())).lastrowid
    @asynccontextmanager
    async def client(self):
        t=httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=t,base_url='http://test') as c:
            r=await c.post('/api/setup',json={'username':'tester','password':'secret123'},headers=HEADERS)
            assert r.status_code==200,r.text
            yield c
    async def queue(self,c,action=None):
        body={'url':URL,'artist':'Meu Canal','track':'Minha Faixa','media_type':'video'}
        if action: body['existing_action']=action
        return await c.post('/api/jobs',json=body,headers=HEADERS)
    def test_old_completed_without_real_file_allows_redownload(self):
        old_id=self.completed(False)
        async def run():
            async with self.client() as c:
                response=await self.queue(c)
                self.assertEqual(response.status_code,200,response.text)
                with main.connect() as db:
                    row=db.execute('SELECT status FROM jobs WHERE id=?',(old_id,)).fetchone()
                self.assertEqual(row['status'],'ausente')
        asyncio.run(run())
    def test_duplicate_requires_choice_and_cancel_has_no_effect(self):
        old_id=self.completed()
        async def run():
            async with self.client() as c:
                response=await self.queue(c)
                self.assertEqual(response.status_code,409,response.text)
                self.assertEqual(response.json()['detail']['code'],'file_exists')
                self.assertEqual(response.json()['detail']['job_id'],old_id)
                with main.connect() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) c FROM jobs').fetchone()['c'],1)
                self.assertEqual(self.old.read_bytes(),b'ORIGINAL')
        asyncio.run(run())
    def test_keep_both_uses_unique_suffix(self):
        self.completed()
        async def run():
            async with self.client() as c:
                response=await self.queue(c,'keep_both')
                self.assertEqual(response.status_code,200,response.text)
                new_id=response.json()['id']
                with patch.object(main.yt_dlp,'YoutubeDL',FakeDL,create=True), patch.object(main,'retag_mp4'):
                    main.download(new_id)
                with main.connect() as db:
                    row=db.execute('SELECT status,path FROM jobs WHERE id=?',(new_id,)).fetchone()
                self.assertEqual(row['status'],'concluido')
                self.assertEqual(row['path'],'Meu Canal/Minha Faixa (2).mp4')
                self.assertEqual(self.old.read_bytes(),b'ORIGINAL')
                self.assertEqual((self.root/row['path']).read_bytes(),b'NOVO_VIDEO_COMPLETO')
        asyncio.run(run())
    def test_replace_keeps_old_on_failure_then_success_is_atomic(self):
        old_id=self.completed()
        async def run():
            async with self.client() as c:
                response=await self.queue(c,'replace')
                new_id=response.json()['id']
                class FailingDL(FakeDL): should_fail=True
                with patch.object(main.yt_dlp,'YoutubeDL',FailingDL,create=True), patch.object(main,'retag_mp4'):
                    main.download(new_id)
                self.assertEqual(self.old.read_bytes(),b'ORIGINAL')
                with main.connect() as db:
                    self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(old_id,)).fetchone()['status'],'concluido')
                r=await self.queue(c,'replace')
                good_id=r.json()['id']
                with patch.object(main.yt_dlp,'YoutubeDL',FakeDL,create=True), patch.object(main,'retag_mp4'):
                    main.download(good_id)
                self.assertEqual(self.old.read_bytes(),b'NOVO_VIDEO_COMPLETO')
                with main.connect() as db:
                    prev=db.execute('SELECT status,path FROM jobs WHERE id=?',(old_id,)).fetchone()
                    new=db.execute('SELECT status,path FROM jobs WHERE id=?',(good_id,)).fetchone()
                self.assertEqual((prev['status'],prev['path']),('substituido',''))
                self.assertEqual((new['status'],new['path']),('concluido','Meu Canal/Minha Faixa.mp4'))
        asyncio.run(run())
    def test_orphan_file_without_history_is_detected(self):
        self.old.write_bytes(b'OUTRO')
        async def run():
            async with self.client() as c:
                r=await self.queue(c)
                self.assertEqual(r.status_code,409)
                self.assertIsNone(r.json()['detail']['job_id'])
        asyncio.run(run())
    def test_ui_controls_exist(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'app/static/index.html').read_text()
        js=(root/'app/static/app.js').read_text()
        for element in ('duplicate-dialog','duplicate-replace','duplicate-keep','duplicate-cancel'):
            self.assertIn('id="'+element+'"',html)
        self.assertIn('function askExistingFile(details)',js)
        self.assertIn('createDownloadJob(',js)
        comp=(root/'compose.yaml').read_text()
        for name in ('YouTube_Music','/Biblioteca/Musicas:/music','/Biblioteca/Videos:/videos','/dados:/config'):
            self.assertIn(name,comp)

if __name__=='__main__': unittest.main()
