/*
Copyright (C) 2025 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/

import React, { useEffect, useMemo, useState } from 'react';
import { API, getLogo, getSystemName, showError, showSuccess } from '../../helpers';
import { marked } from 'marked';
import { Button, Modal, TextArea } from '@douyinfe/semi-ui';

const normalizeBranding = (content, systemName) => {
  if (!content) return content;
  return content
    .replace(/New\s*API/gi, systemName)
    .replace(/new-api/gi, systemName);
};

const About = () => {
  const [about, setAbout] = useState('');
  const [aboutRaw, setAboutRaw] = useState('');
  const [aboutLoaded, setAboutLoaded] = useState(false);
  const [editorVisible, setEditorVisible] = useState(false);
  const [editorValue, setEditorValue] = useState('');
  const [savingAbout, setSavingAbout] = useState(false);
  const logo = getLogo();
  const systemName = getSystemName();
  const isAdmin = useMemo(() => {
    try {
      const raw = localStorage.getItem('user');
      if (!raw) return false;
      const user = JSON.parse(raw);
      return typeof user?.role === 'number' && user.role >= 10;
    } catch (error) {
      return false;
    }
  }, []);

  const displayAbout = async () => {
    setAbout(normalizeBranding(localStorage.getItem('about') || '', systemName));
    const res = await API.get('/api/about');
    const { success, message, data } = res.data;
    if (success) {
      setAboutRaw(typeof data === 'string' ? data : '');
      const normalized = normalizeBranding(data, systemName);
      let aboutContent = normalized;
      if (!normalized.startsWith('https://')) {
        aboutContent = marked.parse(normalized);
      }
      setAbout(aboutContent);
      localStorage.setItem('about', aboutContent);
    } else {
      showError(message);
      setAbout('加载关于内容失败...');
    }
    setAboutLoaded(true);
  };

  const openEditor = () => {
    setEditorValue(aboutRaw || '');
    setEditorVisible(true);
  };

  const submitAbout = async () => {
    setSavingAbout(true);
    try {
      const res = await API.put('/api/option/', {
        key: 'About',
        value: editorValue,
      });
      const { success, message } = res.data;
      if (!success) {
        showError(message || '关于内容更新失败');
        return;
      }
      showSuccess('关于内容已更新');
      setEditorVisible(false);
      await displayAbout();
    } catch (error) {
      showError('关于内容更新失败');
    } finally {
      setSavingAbout(false);
    }
  };

  useEffect(() => {
    displayAbout().then();
  }, []);

  const emptyContent = useMemo(
    () => (
      <div className='min-h-[calc(100vh-64px)] flex flex-col items-center justify-center gap-6 px-6 text-center'>
        <img
          src={logo}
          alt={systemName}
          className='w-full max-w-[280px] object-contain select-none'
        />
        <div className='text-base text-semi-color-text-1'>
          管理员暂时未设置任何关于内容
        </div>
      </div>
    ),
    [logo, systemName],
  );

  if (aboutLoaded && about === '') {
    return (
      <div className='mt-[60px] relative'>
        {isAdmin ? (
          <div className='absolute right-4 top-4 z-10 flex gap-2'>
            <Button type='primary' theme='solid' onClick={openEditor}>
              编辑关于
            </Button>
          </div>
        ) : null}
        {emptyContent}
        <Modal
          title='编辑关于'
          visible={editorVisible}
          onCancel={() => setEditorVisible(false)}
          onOk={submitAbout}
          okText='保存'
          cancelText='取消'
          confirmLoading={savingAbout}
          width={760}
        >
          <TextArea
            value={editorValue}
            onChange={setEditorValue}
            autosize={{ minRows: 12, maxRows: 24 }}
            placeholder='支持 Markdown、HTML，或直接填一个 https:// 链接作为 iframe 页面'
          />
        </Modal>
      </div>
    );
  }

  return (
    <div className='mt-[60px] px-2 relative'>
      {isAdmin ? (
        <div className='flex justify-end mb-3'>
          <Button type='primary' theme='solid' onClick={openEditor}>
            编辑关于
          </Button>
        </div>
      ) : null}
      {about.startsWith('https://') ? (
        <iframe
          src={about}
          style={{ width: '100%', height: '100vh', border: 'none' }}
        />
      ) : (
        <div
          style={{ fontSize: 'larger' }}
          dangerouslySetInnerHTML={{ __html: about }}
        ></div>
      )}
      <Modal
        title='编辑关于'
        visible={editorVisible}
        onCancel={() => setEditorVisible(false)}
        onOk={submitAbout}
        okText='保存'
        cancelText='取消'
        confirmLoading={savingAbout}
        width={760}
      >
        <TextArea
          value={editorValue}
          onChange={setEditorValue}
          autosize={{ minRows: 12, maxRows: 24 }}
          placeholder='支持 Markdown、HTML，或直接填一个 https:// 链接作为 iframe 页面'
        />
      </Modal>
    </div>
  );
};

export default About;
