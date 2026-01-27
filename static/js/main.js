// Global variables
let apiUrl = '';
let currentStarId = null;
let currentPage = 1;
let currentMovieId = null;
let currentMovieKeyword = null;
let currentImages = [];
let currentImageIndex = 0;

// DOM elements
const apiUrlInput = document.getElementById('apiUrlInput');
const checkApiBtn = document.getElementById('checkApiBtn');
const apiAlert = document.getElementById('apiAlert');
const starSearchInput = document.getElementById('starSearchInput');
const starSearchBtn = document.getElementById('starSearchBtn');
const searchProgress = document.getElementById('searchProgress');
const starsList = document.getElementById('starsList');
const searchModeButtons = document.getElementById('searchModeButtons');
const titleSearchRadio = document.getElementById('titleSearchRadio');
const allMoviesRadio = document.getElementById('allMoviesRadio');
const starInfoCard = document.getElementById('starInfoCard');
const starAvatar = document.getElementById('starAvatar');
const starDetails = document.getElementById('starDetails');
const moviesTable = document.getElementById('moviesTable');
const tableLoading = document.getElementById('tableLoading');
const noMoviesMsg = document.getElementById('noMoviesMsg');
const paginationControls = document.getElementById('paginationControls');
const prevPageBtn = document.getElementById('prevPageBtn');
const pageInfo = document.getElementById('pageInfo');
const nextPageBtn = document.getElementById('nextPageBtn');
const refreshBtn = document.getElementById('refreshBtn');
const movieDetailsPanel = document.getElementById('movieDetailsPanel');
const movieTitle = document.getElementById('movieTitle');
const previewImage = document.getElementById('previewImage');
const imageCounter = document.getElementById('imageCounter');
const prevImageBtn = document.getElementById('prevImageBtn');
const nextImageBtn = document.getElementById('nextImageBtn');
const movieProducerInfo = document.getElementById('movieProducerInfo');
const movieGenresInfo = document.getElementById('movieGenresInfo');
const movieSummaryInfo = document.getElementById('movieSummaryInfo');
const movieStarsInfo = document.getElementById('movieStarsInfo');
const magnetsList = document.getElementById('magnetsList');
const copyMagnetBtn = document.getElementById('copyMagnetBtn');
const movieSearchInput = document.getElementById('movieSearchInput');
const movieSearchBtn = document.getElementById('movieSearchBtn');
const magnetOnlyCheckbox = document.getElementById('magnetOnlyCheckbox');
const keywordSearchInput = document.getElementById('keywordSearchInput');
const keywordSearchBtn = document.getElementById('keywordSearchBtn');

// Initialize the page
document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips if Bootstrap is loaded
    if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggerList.map(function (tooltipTriggerEl) {
            return new bootstrap.Tooltip(tooltipTriggerEl);
        });
    }

    // Get API URL from input
    apiUrl = apiUrlInput ? apiUrlInput.value.trim() : '';
    if (apiUrl) {
        checkApiConnection();
    }

    // Set up event listeners
    setupEventListeners();
});

// Set up event listeners
function setupEventListeners() {
    // API URL checking
    if (checkApiBtn) {
        checkApiBtn.addEventListener('click', function() {
            apiUrl = apiUrlInput.value.trim();
            checkApiConnection();
        });
    }

    // Star search
    if (starSearchBtn) {
        starSearchBtn.addEventListener('click', searchStars);
    }
    
    if (starSearchInput) {
        starSearchInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                searchStars();
            }
        });
    }

    // Movie search
    if (movieSearchBtn) {
        movieSearchBtn.addEventListener('click', searchMovies);
    }
    
    if (movieSearchInput) {
        movieSearchInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                searchMovies();
            }
        });
    }
    
    // Keyword search
    if (keywordSearchBtn) {
        keywordSearchBtn.addEventListener('click', searchByKeyword);
    }
    
    if (keywordSearchInput) {
        keywordSearchInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                searchByKeyword();
            }
        });
    }

    // Pagination
    if (prevPageBtn) {
        prevPageBtn.addEventListener('click', loadPrevPage);
    }
    
    if (nextPageBtn) {
        nextPageBtn.addEventListener('click', loadNextPage);
    }

    // Refresh button
    if (refreshBtn) {
        refreshBtn.addEventListener('click', refreshStarData);
    }

    // Image navigation
    if (prevImageBtn) {
        prevImageBtn.addEventListener('click', showPrevImage);
    }
    
    if (nextImageBtn) {
        nextImageBtn.addEventListener('click', showNextImage);
    }

    // Copy magnet link
    if (copyMagnetBtn) {
        copyMagnetBtn.addEventListener('click', copySelectedMagnet);
    }

    // Home link
    const homeLink = document.getElementById('homeLink');
    if (homeLink) {
        homeLink.addEventListener('click', function(e) {
            e.preventDefault();
            resetState();
        });
    }
}

// Search by keyword
function searchByKeyword() {
    if (!keywordSearchInput) return;
    
    const keyword = keywordSearchInput.value.trim();
    if (!keyword) {
        alert('请输入关键字');
        return;
    }

    // 重定向到关键字搜索页面
    window.location.href = `/search_keyword?keyword=${encodeURIComponent(keyword)}`;
}

// Check API connection
function checkApiConnection() {
    // Show loading state
    showApiStatus('info', 'Checking API connection...');
    
    // Make API request
    fetch(`/api/check_connection?api_url=${encodeURIComponent(apiUrl)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                showApiStatus('success', data.message || 'API connection successful');
                // Save API URL in localStorage for future use
                localStorage.setItem('apiUrl', apiUrl);
            } else {
                showApiStatus('danger', data.message || 'API connection failed');
            }
        })
        .catch(error => {
            showApiStatus('danger', `Error checking API: ${error.message}`);
        });
}

// Function to update API status display
function updateApiStatus() {
    const apiAlert = document.getElementById('apiAlert');
    const apiUrlInput = document.getElementById('apiUrlInput');
    
    if (!apiAlert || !apiUrlInput) return;
    
    const apiUrl = apiUrlInput.value.trim();
    if (apiUrl) {
        showApiStatus('info', 'Click "Check API" to verify the connection');
    } else {
        showApiStatus('warning', 'Please enter an API URL');
    }
}

// Function to show API status with appropriate styling
function showApiStatus(type, message) {
    const apiAlert = document.getElementById('apiAlert');
    if (!apiAlert) return;
    
    // Remove existing alert classes
    apiAlert.classList.remove('alert-success', 'alert-danger', 'alert-warning', 'alert-info');
    // Add the appropriate alert class
    apiAlert.classList.add(`alert-${type}`);
    // Set the message
    apiAlert.textContent = message;
}

// Search stars
function searchStars() {
    const keyword = starSearchInput.value.trim();
    if (!keyword) {
        alert('请输入演员名称');
        return;
    }

    if (!apiUrl) {
        alert('请先设置API地址');
        return;
    }

    // Show progress bar
    searchProgress.style.display = 'block';
    starsList.innerHTML = '';
    starInfoCard.style.display = 'none';
    moviesTable.querySelector('tbody').innerHTML = '';
    noMoviesMsg.style.display = 'none';
    paginationControls.style.display = 'none';
    movieDetailsPanel.style.display = 'none';

    fetch(`/api/search_stars?keyword=${encodeURIComponent(keyword)}&api_url=${encodeURIComponent(apiUrl)}`)
        .then(response => response.json())
        .then(data => {
            searchProgress.style.display = 'none';
            if (data.status === 'success') {
                const stars = data.stars;
                if (stars && stars.length > 0) {
                    stars.forEach(star => {
                        const item = document.createElement('a');
                        item.className = 'list-group-item list-group-item-action';
                        item.textContent = `${star.name} (${star.id})`;
                        item.dataset.id = star.id;
                        item.dataset.name = star.name;
                        item.addEventListener('click', function() {
                            onStarSelected(this.dataset.id, this.dataset.name);
                        });
                        starsList.appendChild(item);
                    });
                    // Show search mode buttons
                    searchModeButtons.style.display = 'flex';
                } else {
                    starsList.innerHTML = '<div class="list-group-item">未找到匹配的演员</div>';
                }
            } else {
                starsList.innerHTML = `<div class="list-group-item text-danger">搜索出错: ${data.message}</div>`;
            }
        })
        .catch(error => {
            searchProgress.style.display = 'none';
            starsList.innerHTML = `<div class="list-group-item text-danger">搜索出错: ${error.message}</div>`;
        });
}

// Handle star selection
function onStarSelected(starId, starName) {
    currentStarId = starId;
    currentPage = 1;
    refreshBtn.style.display = 'block';

    // Load star info
    loadStarInfo(starId);

    // Load star movies
    loadStarMovies(starId, starName, currentPage);
}

// Load star info
function loadStarInfo(starId) {
    starInfoCard.style.display = 'block';
    starAvatar.src = '';
    starDetails.innerHTML = '<div class="text-center">加载中...</div>';

    fetch(`/api/star/${starId}?api_url=${encodeURIComponent(apiUrl)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const star = data.star;
                
                // Set avatar
                if (star.avatar) {
                    starAvatar.src = star.avatar;
                } else {
                    starAvatar.src = '/static/img/no-avatar.jpg';
                }

                // Build details HTML
                let detailsHtml = `
                    <p><strong>姓名:</strong> ${star.name || '未知'}</p>
                    <p><strong>生日:</strong> ${star.birthday || '未知'}</p>
                    <p><strong>年龄:</strong> ${star.age || '未知'}</p>
                    <p><strong>身高:</strong> ${star.height || '未知'}</p>
                    <p><strong>胸围:</strong> ${star.bust || '未知'}</p>
                    <p><strong>腰围:</strong> ${star.waistline || '未知'}</p>
                    <p><strong>臀围:</strong> ${star.hipline || '未知'}</p>
                    <p><strong>出生地:</strong> ${star.birthplace || '未知'}</p>
                    <p><strong>爱好:</strong> ${star.hobby || '未知'}</p>
                `;
                starDetails.innerHTML = detailsHtml;
            } else {
                starDetails.innerHTML = `<div class="text-danger">获取演员信息失败: ${data.message}</div>`;
            }
        })
        .catch(error => {
            starDetails.innerHTML = `<div class="text-danger">获取演员信息出错: ${error.message}</div>`;
        });
}

// Load star movies
function loadStarMovies(starId, starName, page) {
    tableLoading.style.display = 'block';
    moviesTable.querySelector('tbody').innerHTML = '';
    noMoviesMsg.style.display = 'none';
    paginationControls.style.display = 'none';

    const titleSearch = titleSearchRadio.checked;
    const magnetOnly = magnetOnlyCheckbox.checked;

    fetch(`/api/star/${starId}/movies?api_url=${encodeURIComponent(apiUrl)}&page=${page}&title_search=${titleSearch}&magnet_only=${magnetOnly}&star_name=${encodeURIComponent(starName)}`)
        .then(response => response.json())
        .then(data => {
            tableLoading.style.display = 'none';
            
            if (data.status === 'success') {
                const movies = data.movies;
                const pagination = data.pagination;
                
                if (movies && movies.length > 0) {
                    // Sort movies by date (newest first)
                    const sortedMovies = movies.sort((a, b) => {
                        return new Date(b.date || 0) - new Date(a.date || 0);
                    });
                    
                    // Populate table
                    const tbody = moviesTable.querySelector('tbody');
                    sortedMovies.forEach(movie => {
                        const row = document.createElement('tr');
                        row.style.cursor = 'pointer';
                        row.dataset.id = movie.id;
                        
                        let title = movie.title || '';
                        if (title.startsWith(movie.id)) {
                            title = title.substring(movie.id.length).trim();
                        }
                        
                        row.innerHTML = `
                            <td>${movie.id}</td>
                            <td title="${title}">${title}</td>
                            <td>${movie.date || ''}</td>
                        `;
                        
                        row.addEventListener('click', function() {
                            onMovieSelected(this.dataset.id);
                        });
                        
                        tbody.appendChild(row);
                    });
                    
                    // Update pagination
                    updatePagination(pagination);
                } else {
                    noMoviesMsg.style.display = 'block';
                }
            } else {
                noMoviesMsg.style.display = 'block';
                noMoviesMsg.textContent = `获取影片列表失败: ${data.message}`;
                noMoviesMsg.className = 'alert alert-danger';
            }
        })
        .catch(error => {
            tableLoading.style.display = 'none';
            noMoviesMsg.style.display = 'block';
            noMoviesMsg.textContent = `获取影片列表出错: ${error.message}`;
            noMoviesMsg.className = 'alert alert-danger';
        });
}

// Update pagination controls
function updatePagination(pagination) {
    if (!pagination) return;
    
    const currentPage = pagination.currentPage || 1;
    const hasNextPage = pagination.hasNextPage || false;
    const totalPages = pagination.totalPages || currentPage;
    
    pageInfo.textContent = `第${currentPage}页/共${totalPages}页`;
    prevPageBtn.disabled = currentPage <= 1;
    nextPageBtn.disabled = !hasNextPage;
    
    paginationControls.style.display = 'flex';
}

// Load previous page
function loadPrevPage() {
    if (currentPage > 1) {
        currentPage--;
        if (currentStarId) {
            // Get star name from selected star
            const selectedStar = document.querySelector(`#starsList a[data-id="${currentStarId}"]`);
            const starName = selectedStar ? selectedStar.dataset.name : '';
            loadStarMovies(currentStarId, starName, currentPage);
        } else if (currentMovieKeyword) {
            searchMovies(currentPage);
        }
    }
}

// Load next page
function loadNextPage() {
    currentPage++;
    if (currentStarId) {
        // Get star name from selected star
        const selectedStar = document.querySelector(`#starsList a[data-id="${currentStarId}"]`);
        const starName = selectedStar ? selectedStar.dataset.name : '';
        loadStarMovies(currentStarId, starName, currentPage);
    } else if (currentMovieKeyword) {
        searchMovies(currentPage);
    }
}

// Refresh star data
function refreshStarData() {
    if (!currentStarId) return;
    
    if (!confirm('确定要刷新当前演员的数据吗？')) return;
    
    // Get star name from selected star
    const selectedStar = document.querySelector(`#starsList a[data-id="${currentStarId}"]`);
    const starName = selectedStar ? selectedStar.dataset.name : '';
    
    // Reload data
    loadStarInfo(currentStarId);
    loadStarMovies(currentStarId, starName, currentPage);
}

// Handle movie selection
function onMovieSelected(movieId) {
    currentMovieId = movieId;
    movieDetailsPanel.style.display = 'block';
    
    // Reset image navigation
    currentImages = [];
    currentImageIndex = 0;
    previewImage.src = '';
    imageCounter.textContent = '0/0';
    prevImageBtn.disabled = true;
    nextImageBtn.disabled = true;
    
    // Reset other UI elements
    movieTitle.textContent = '加载中...';
    movieProducerInfo.textContent = '厂牌: 加载中...';
    movieGenresInfo.textContent = '类别: 加载中...';
    movieSummaryInfo.textContent = '简介: 加载中...';
    movieStarsInfo.textContent = '演员: 加载中...';
    magnetsList.innerHTML = '';
    copyMagnetBtn.disabled = true;
    
    // Load movie details
    loadMovieDetails(movieId);
    
    // Load movie images
    loadMovieImages(movieId);
    
    // Load movie magnets
    loadMovieMagnets(movieId);
}

// Load movie details
function loadMovieDetails(movieId) {
    fetch(`/api/movie/${movieId}?api_url=${encodeURIComponent(apiUrl)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const movie = data.movie;
                
                // Update title
                const title = movie.title || 'Unknown Title';
                movieTitle.textContent = title;
                
                // Update producer info
                const producer = movie.producer || {};
                const producerName = producer.name || 'Unknown';
                const producerId = producer.id || '';
                movieProducerInfo.innerHTML = `厂牌: <a href="#" data-id="${producerId}">${producerName}</a>`;
                
                // Update genres info
                const genres = movie.genres || [];
                let genresHtml = '类别: ';
                genres.forEach((genre, index) => {
                    const genreName = genre.name || '';
                    const genreId = genre.id || '';
                    if (index > 0) genresHtml += ', ';
                    genresHtml += `<a href="#" data-id="${genreId}">${genreName}</a>`;
                });
                movieGenresInfo.innerHTML = genresHtml;
                
                // Update summary info
                const summary = movie.summary || 'No summary available';
                movieSummaryInfo.innerHTML = `简介: ${summary}`;
                
                // Update stars info
                const stars = movie.stars || [];
                let starsHtml = '演员: ';
                stars.forEach((star, index) => {
                    const starName = star.name || '';
                    const starId = star.id || '';
                    if (index > 0) starsHtml += ', ';
                    starsHtml += `<a href="#" data-id="${starId}" data-name="${starName}">${starName}</a>`;
                });
                movieStarsInfo.innerHTML = starsHtml;
                
                // Add click event listeners to star links
                const starLinks = movieStarsInfo.querySelectorAll('a');
                starLinks.forEach(link => {
                    link.addEventListener('click', function(e) {
                        e.preventDefault();
                        onStarSelected(this.dataset.id, this.dataset.name);
                    });
                });
            } else {
                movieTitle.textContent = 'Error loading movie details';
                movieSummaryInfo.textContent = `简介: ${data.message}`;
            }
        })
        .catch(error => {
            movieTitle.textContent = 'Error loading movie details';
            movieSummaryInfo.textContent = `简介: ${error.message}`;
        });
}

// Load movie images
function loadMovieImages(movieId) {
    fetch(`/api/movie/${movieId}/images?api_url=${encodeURIComponent(apiUrl)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const images = data.images;
                
                if (images && images.length > 0) {
                    // Sort images (cover first, then samples)
                    const coverImages = images.filter(img => img.type === 'cover');
                    const sampleImages = images.filter(img => img.type === 'sample');
                    currentImages = [...coverImages, ...sampleImages];
                    
                    // Display first image
                    currentImageIndex = 0;
                    displayCurrentImage();
                } else {
                    previewImage.src = '/static/img/no-image.jpg';
                    imageCounter.textContent = '0/0';
                }
            } else {
                previewImage.src = '/static/img/no-image.jpg';
                imageCounter.textContent = `Error: ${data.message}`;
            }
        })
        .catch(error => {
            previewImage.src = '/static/img/no-image.jpg';
            imageCounter.textContent = `Error: ${error.message}`;
        });
}

// Load movie magnets
function loadMovieMagnets(movieId) {
    fetch(`/api/movie/${movieId}/magnets?api_url=${encodeURIComponent(apiUrl)}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const magnets = data.magnets;
                
                if (magnets && magnets.length > 0) {
                    magnetsList.innerHTML = '';
                    
                    magnets.forEach(magnet => {
                        const title = magnet.title || '';
                        const link = magnet.link || '';
                        const size = magnet.size || '';
                        const date = magnet.shareDate || magnet.date || '';
                        const hasSubtitle = magnet.hasSubtitle || false;
                        const isHD = magnet.isHD || false;
                        
                        const subtitleMark = hasSubtitle ? '[中字]' : '';
                        const hdMark = isHD ? '[HD]' : '';
                        
                        const item = document.createElement('a');
                        item.className = 'list-group-item list-group-item-action';
                        item.textContent = `${title} ${subtitleMark} ${hdMark} [${size}] (${date})`;
                        item.dataset.link = link;
                        
                        // Set different colors based on magnet type
                        if (hasSubtitle) {
                            item.style.color = 'green';
                        } else if (isHD) {
                            item.style.color = 'blue';
                        }
                        
                        item.addEventListener('click', function() {
                            // Select this item
                            const allItems = magnetsList.querySelectorAll('a');
                            allItems.forEach(item => item.classList.remove('active'));
                            this.classList.add('active');
                            copyMagnetBtn.disabled = false;
                        });
                        
                        item.addEventListener('dblclick', function() {
                            copyMagnetLink(this.dataset.link);
                        });
                        
                        magnetsList.appendChild(item);
                    });
                } else {
                    magnetsList.innerHTML = '<div class="list-group-item">没有磁力链接</div>';
                }
            } else {
                magnetsList.innerHTML = `<div class="list-group-item text-danger">获取磁力链接失败: ${data.message}</div>`;
            }
        })
        .catch(error => {
            magnetsList.innerHTML = `<div class="list-group-item text-danger">获取磁力链接出错: ${error.message}</div>`;
        });
}

// Display current image
function displayCurrentImage() {
    if (currentImages.length === 0) return;
    
    const currentImage = currentImages[currentImageIndex];
    previewImage.src = currentImage.path;
    imageCounter.textContent = `${currentImageIndex + 1}/${currentImages.length}`;
    
    prevImageBtn.disabled = currentImageIndex === 0;
    nextImageBtn.disabled = currentImageIndex === currentImages.length - 1;
}

// Show previous image
function showPrevImage() {
    if (currentImageIndex > 0) {
        currentImageIndex--;
        displayCurrentImage();
    }
}

// Show next image
function showNextImage() {
    if (currentImageIndex < currentImages.length - 1) {
        currentImageIndex++;
        displayCurrentImage();
    }
}

// Copy selected magnet link
function copySelectedMagnet() {
    const selectedItem = magnetsList.querySelector('a.active');
    if (selectedItem) {
        copyMagnetLink(selectedItem.dataset.link);
    }
}

// Copy magnet link to clipboard
function copyMagnetLink(link) {
    if (!link) return;
    
    // Create temporary textarea to hold the link
    const textarea = document.createElement('textarea');
    textarea.value = link;
    document.body.appendChild(textarea);
    textarea.select();
    
    try {
        // Execute copy command
        const successful = document.execCommand('copy');
        if (successful) {
            alert('磁力链接已复制到剪贴板');
        } else {
            alert('复制失败，请手动复制');
        }
    } catch (err) {
        alert('复制出错: ' + err);
    }
    
    document.body.removeChild(textarea);
}

// Search movies
function searchMovies(page = 1) {
    const keyword = movieSearchInput.value.trim();
    if (!keyword) {
        alert('请输入搜索关键词');
        return;
    }

    if (!apiUrl) {
        alert('请先设置API地址');
        return;
    }

    currentMovieKeyword = keyword;
    currentPage = page;
    currentStarId = null;
    starInfoCard.style.display = 'none';
    refreshBtn.style.display = 'none';
    movieDetailsPanel.style.display = 'none';
    tableLoading.style.display = 'block';
    moviesTable.querySelector('tbody').innerHTML = '';
    noMoviesMsg.style.display = 'none';
    paginationControls.style.display = 'none';

    const magnetOnly = magnetOnlyCheckbox.checked;

    fetch(`/api/search_movies?keyword=${encodeURIComponent(keyword)}&api_url=${encodeURIComponent(apiUrl)}&page=${page}&magnet_only=${magnetOnly}`)
        .then(response => response.json())
        .then(data => {
            tableLoading.style.display = 'none';
            
            if (data.status === 'success') {
                const movies = data.movies;
                const pagination = data.pagination;
                
                if (movies && movies.length > 0) {
                    // Populate table
                    const tbody = moviesTable.querySelector('tbody');
                    movies.forEach(movie => {
                        const row = document.createElement('tr');
                        row.style.cursor = 'pointer';
                        row.dataset.id = movie.id;
                        
                        let title = movie.title || '';
                        if (title.startsWith(movie.id)) {
                            title = title.substring(movie.id.length).trim();
                        }
                        
                        row.innerHTML = `
                            <td>${movie.id}</td>
                            <td title="${title}">${title}</td>
                            <td>${movie.date || ''}</td>
                        `;
                        
                        row.addEventListener('click', function() {
                            onMovieSelected(this.dataset.id);
                        });
                        
                        tbody.appendChild(row);
                    });
                    
                    // Update pagination
                    updatePagination(pagination);
                } else {
                    noMoviesMsg.style.display = 'block';
                    noMoviesMsg.textContent = '未找到影片';
                    noMoviesMsg.className = 'alert alert-info';
                }
            } else {
                noMoviesMsg.style.display = 'block';
                noMoviesMsg.textContent = `搜索失败: ${data.message}`;
                noMoviesMsg.className = 'alert alert-danger';
            }
        })
        .catch(error => {
            tableLoading.style.display = 'none';
            noMoviesMsg.style.display = 'block';
            noMoviesMsg.textContent = `搜索出错: ${error.message}`;
            noMoviesMsg.className = 'alert alert-danger';
        });
}

// Reset page state
function resetState() {
    currentStarId = null;
    currentPage = 1;
    currentMovieId = null;
    currentMovieKeyword = null;
    currentImages = [];
    currentImageIndex = 0;
    
    starInfoCard.style.display = 'none';
    movieDetailsPanel.style.display = 'none';
    refreshBtn.style.display = 'none';
    starSearchInput.value = '';
    movieSearchInput.value = '';
    starsList.innerHTML = '';
    searchModeButtons.style.display = 'none';
    moviesTable.querySelector('tbody').innerHTML = '';
    noMoviesMsg.style.display = 'none';
    paginationControls.style.display = 'none';
}

// Function to copy text to clipboard
function copyToClipboard(text) {
    if (!navigator.clipboard) {
        // Fallback for older browsers
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        
        try {
            const successful = document.execCommand('copy');
            return successful;
        } catch (err) {
            console.error('Fallback: Could not copy text: ', err);
            return false;
        } finally {
            document.body.removeChild(textArea);
        }
    }
    
    // Modern browsers
    return navigator.clipboard.writeText(text)
        .then(() => true)
        .catch(err => {
            console.error('Could not copy text: ', err);
            return false;
        });
}

// Function to handle translation requests
function translateText(text, elementId) {
    if (!text) return;
    
    const targetElement = document.getElementById(elementId);
    if (!targetElement) return;
    
    // Show loading state
    targetElement.innerHTML = '<div class="spinner-border spinner-border-sm text-primary" role="status"><span class="visually-hidden">Loading...</span></div> Translating...';
    
    // Make API request for translation
    fetch('/api/translate', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ text: text })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success' && data.translated_text) {
            targetElement.textContent = data.translated_text;
        } else {
            targetElement.textContent = data.message || 'Translation failed';
            targetElement.classList.add('text-danger');
        }
    })
    .catch(error => {
        targetElement.textContent = `Error: ${error.message}`;
        targetElement.classList.add('text-danger');
    });
} 