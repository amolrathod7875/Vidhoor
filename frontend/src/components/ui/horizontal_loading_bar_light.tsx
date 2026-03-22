import React from 'react';
import styled from 'styled-components';

const Loader = () => {
  return (
    <StyledWrapper>
      <div className="loader" />
    </StyledWrapper>
  );
}

const StyledWrapper = styled.div`
  width: 100%;

  .loader {
    --height-of-loader: 4px;
    --loader-color: #0071e2;
    width: 100%;
    height: var(--height-of-loader);
    border-radius: 30px;
    background-color: rgba(0,0,0,0.2);
    position: relative;
    overflow: hidden;
  }

  .loader::before {
    content: "";
    position: absolute;
    background: var(--loader-color);
    top: 0;
    left: -35%;
    width: 35%;
    height: 100%;
    border-radius: 30px;
    animation: moving 1.1s ease-in-out infinite;
  }

  @keyframes moving {
    0% { left: -35%; }
    50% { left: 35%; }
    100% { left: 100%; }
  }`;

export default Loader;
