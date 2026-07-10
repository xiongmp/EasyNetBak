document.addEventListener('DOMContentLoaded', function() {
            const themeToggle = document.getElementById('themeToggle');
            const icon = themeToggle.querySelector('i');
            
            function updateIcon(theme) {
              if (theme === 'dark') {
                icon.className = 'bi bi-sun';
              } else {
                icon.className = 'bi bi-moon-stars';
              }
            }

            // 初始化图标
            const currentTheme = document.documentElement.getAttribute('data-bs-theme') || 'light';
            updateIcon(currentTheme);

            // 全屏切换
            const fullscreenToggle = document.getElementById('fullscreenToggle');
            const fullscreenIcon = fullscreenToggle.querySelector('i');
            
            fullscreenToggle.addEventListener('click', function() {
              if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(err => {
                  console.error(`Error attempting to enable full-screen mode: ${err.message}`);
                });
              } else {
                if (document.exitFullscreen) {
                  document.exitFullscreen();
                }
              }
            });

            document.addEventListener('fullscreenchange', function() {
              const isAppFullscreen = Boolean(document.fullscreenElement);
              document.body.classList.toggle('dashboard-fullscreen', isAppFullscreen && document.body.classList.contains('dashboard-page'));
              
              if (isAppFullscreen) {
                fullscreenIcon.className = 'bi bi-fullscreen-exit';
                fullscreenToggle.setAttribute('title', '退出全屏');
              } else {
                fullscreenIcon.className = 'bi bi-arrows-fullscreen';
                fullscreenToggle.setAttribute('title', '全屏显示');
              }

              window.dispatchEvent(new CustomEvent('appFullscreenChanged', {
                detail: { isFullscreen: isAppFullscreen }
              }));
              
              // 全屏切换后触发 resize 事件，确保图表等组件适配新尺寸
              setTimeout(() => {
                window.dispatchEvent(new Event('resize'));
              }, 300);
            });

            themeToggle.addEventListener('click', function(event) {
              const currentTheme = document.documentElement.getAttribute('data-bs-theme') || 'light';
              const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
              
              const toggleTheme = () => {
                document.documentElement.setAttribute('data-bs-theme', newTheme);
                localStorage.setItem('theme', newTheme);
                updateIcon(newTheme);
                
                // Dispatch theme change event for other components (e.g., ECharts)
                const themeEvent = new CustomEvent('themeChanged', { detail: { theme: newTheme } });
                window.dispatchEvent(themeEvent);
              };

              if (!document.startViewTransition) {
                toggleTheme();
                return;
              }

              const x = event.clientX;
              const y = event.clientY;
              const endRadius = Math.hypot(
                Math.max(x, innerWidth - x),
                Math.max(y, innerHeight - y)
              );

              const transition = document.startViewTransition(() => {
                toggleTheme();
              });

              transition.ready.then(() => {
                const clipPath = [
                  `circle(0px at ${x}px ${y}px)`,
                  `circle(${endRadius}px at ${x}px ${y}px)`,
                ];
                document.documentElement.animate(
                  {
                    clipPath: clipPath,
                  },
                  {
                    duration: 400,
                    easing: 'ease-in-out',
                    pseudoElement: '::view-transition-new(root)',
                  }
                );
              });
            });

            // 侧边栏折叠
            const sidebarToggle = document.getElementById('sidebarToggle');
            const sidebar = document.querySelector('.sidebar');
            const toggleIcon = sidebarToggle.querySelector('i');
            
            function updateToggleIcon(isCollapsed) {
              if (isCollapsed) {
                toggleIcon.className = 'bi bi-text-indent-left';
                sidebarToggle.setAttribute('title', '展开菜单');
              } else {
                toggleIcon.className = 'bi bi-list';
                sidebarToggle.setAttribute('title', '折叠菜单');
              }
            }

            // 从 localStorage 读取状态 (这一步其实在 aside 标签处已经通过 inline script 处理了 class，这里同步一下变量和图标)
            const isCollapsed = sidebar.classList.contains('collapsed');
            updateToggleIcon(isCollapsed);

            sidebarToggle.addEventListener('click', function() {
              sidebar.classList.toggle('collapsed');
              const isNowCollapsed = sidebar.classList.contains('collapsed');
              localStorage.setItem('sidebarCollapsed', isNowCollapsed);
              updateToggleIcon(isNowCollapsed);
              
              // 切换折叠状态时，如果是收起状态，禁用 Bootstrap 的 collapse 自动触发
              document.querySelectorAll('.sidebar-group-label').forEach(label => {
                if (isNowCollapsed) {
                  label.removeAttribute('data-bs-toggle');
                } else {
                  label.setAttribute('data-bs-toggle', 'collapse');
                }
              });

              // 触发 resize 事件以更新图表等组件
              // 由于侧边栏有 0.3s 的过渡动画，我们在动画开始和结束时都触发一次
              window.dispatchEvent(new Event('resize'));
              setTimeout(() => {
                window.dispatchEvent(new Event('resize'));
              }, 350);
            });

            // 初始化时也执行一次
            if (isCollapsed) {
              document.querySelectorAll('.sidebar-group-label').forEach(label => {
                label.removeAttribute('data-bs-toggle');
              });
            }
          });
