import React, { Component, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import Nav from '../../src/components/Nav';
import Hero from '../../src/components/Hero';
import ShadowSection from '../../src/components/ShadowSection';
import Features from '../../src/components/Features';
import { ThemeProvider } from '../../src/theme';
import '../../src/index.css';
class SceneFallback extends Component<{children:ReactNode},{failed:boolean}>{state={failed:false};static getDerivedStateFromError(){return {failed:true}}render(){return this.state.failed?<img src="/docs/media/landing-axon.png" alt="Historical original landing capture; WebGL unavailable in this browser" style={{width:'100%'}}/>:this.props.children}}
createRoot(document.getElementById('root')!).render(<ThemeProvider><Nav onOpenPalette={()=>{}}/><main><SceneFallback><Hero onOpenPalette={()=>{}}/></SceneFallback><ShadowSection/><Features/></main></ThemeProvider>);
