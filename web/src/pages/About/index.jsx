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
import { API, getLogo, getSystemName, showError } from '../../helpers';
import { marked } from 'marked';

const normalizeBranding = (content, systemName) => {
  if (!content) return content;
  return content
    .replace(/New\s*API/gi, systemName)
    .replace(/new-api/gi, systemName);
};

const About = () => {
  const [about, setAbout] = useState('');
  const [aboutLoaded, setAboutLoaded] = useState(false);
  const logo = getLogo();
  const systemName = getSystemName();

  const displayAbout = async () => {
    setAbout(normalizeBranding(localStorage.getItem('about') || '', systemName));
    const res = await API.get('/api/about');
    const { success, message, data } = res.data;
    if (success) {
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
    return <div className='mt-[60px]'>{emptyContent}</div>;
  }

  return (
    <div className='mt-[60px] px-2'>
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
    </div>
  );
};

export default About;
