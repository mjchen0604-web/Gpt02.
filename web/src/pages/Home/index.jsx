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

import React from 'react';
import { getLogo, getSystemName } from '../../helpers';

const Home = () => {
  const logo = getLogo();
  const systemName = getSystemName();

  return (
    <div className='w-full min-h-[calc(100vh-64px)] flex items-center justify-center px-6 py-10 overflow-hidden'>
      <div className='flex flex-col items-center justify-center text-center'>
        <img
          src={logo}
          alt={systemName}
          className='w-full max-w-[560px] object-contain select-none'
        />
        <div className='mt-8 text-[28px] md:text-[36px] font-black tracking-[0.08em] text-black'>
          只做纯满血的API
        </div>
      </div>
    </div>
  );
};

export default Home;
