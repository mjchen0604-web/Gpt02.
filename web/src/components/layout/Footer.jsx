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
import { Typography } from '@douyinfe/semi-ui';
import { getFooterHTML, getLogo, getSystemName } from '../../helpers';

const normalizeBranding = (content, systemName) => {
  if (!content) return content;
  return content
    .replace(/New\s*API/gi, systemName)
    .replace(/new-api/gi, systemName);
};

const FooterBar = () => {
  const [footer, setFooter] = useState(getFooterHTML());
  const systemName = getSystemName();
  const logo = getLogo();
  const currentYear = new Date().getFullYear();

  useEffect(() => {
    const footerHTML = localStorage.getItem('footer_html');
    if (footerHTML) {
      setFooter(footerHTML);
    }
  }, []);

  const normalizedFooter = useMemo(
    () => normalizeBranding(footer, systemName),
    [footer, systemName],
  );

  const defaultFooter = useMemo(
    () => (
      <footer className='relative h-auto py-12 px-6 md:px-24 w-full flex flex-col items-center justify-center gap-5 overflow-hidden'>
        <img
          src={logo}
          alt={systemName}
          className='w-16 h-16 rounded-full bg-white p-1 object-contain'
        />
        <Typography.Text className='text-sm !text-semi-color-text-1'>
          © {currentYear} {systemName}. 版权所有
        </Typography.Text>
        <Typography.Text className='text-sm !text-semi-color-text-1'>
          设计与开发由 {systemName}
        </Typography.Text>
      </footer>
    ),
    [currentYear, logo, systemName],
  );

  return (
    <div className='w-full'>
      {normalizedFooter ? (
        <div className='relative'>
          <div
            className='custom-footer'
            dangerouslySetInnerHTML={{ __html: normalizedFooter }}
          ></div>
          <div className='absolute bottom-2 right-4 text-xs !text-semi-color-text-2 opacity-70'>
            <span>设计与开发由 {systemName}</span>
          </div>
        </div>
      ) : (
        defaultFooter
      )}
    </div>
  );
};

export default FooterBar;
