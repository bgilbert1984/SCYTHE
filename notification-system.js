// RF SCYTHE notification system
// This provides a centralized way to show notifications to the user

// Create the notification container if it doesn't exist
function createNotificationContainer() {
    let container = document.getElementById('rf-scythe-notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'rf-scythe-notification-container';
        container.style.position = 'fixed';
        container.style.top = '20px';
        container.style.right = '20px';
        container.style.zIndex = '9999';
        container.style.width = '300px';
        document.body.appendChild(container);
    }
    return container;
}

// Show a notification
function showNotification(message, type = 'info', duration = 5000) {
    const container = createNotificationContainer();
    
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `rf-scythe-notification ${type}`;
    notification.style.backgroundColor = type === 'error' ? '#ff3b30' : 
                                         type === 'warning' ? '#ff9500' : 
                                         type === 'success' ? '#34c759' : '#007aff';
    notification.style.color = '#ffffff';
    notification.style.padding = '12px 16px';
    notification.style.marginBottom = '10px';
    notification.style.borderRadius = '6px';
    notification.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.15)';
    notification.style.transition = 'all 0.3s ease';
    notification.style.opacity = '0';
    notification.style.transform = 'translateY(-20px)';
    
    // Add close button
    const closeButton = document.createElement('span');
    closeButton.innerHTML = '×';
    closeButton.style.float = 'right';
    closeButton.style.cursor = 'pointer';
    closeButton.style.marginLeft = '10px';
    closeButton.style.fontWeight = 'bold';
    closeButton.onclick = function() {
        removeNotification(notification);
    };
    
    notification.textContent = message;
    notification.appendChild(closeButton);
    container.appendChild(notification);
    
    // Animate in
    setTimeout(() => {
        notification.style.opacity = '1';
        notification.style.transform = 'translateY(0)';
    }, 10);
    
    // Auto remove after duration
    if (duration > 0) {
        setTimeout(() => {
            removeNotification(notification);
        }, duration);
    }
    
    return notification;
}

// Remove a notification with animation
function removeNotification(notification) {
    notification.style.opacity = '0';
    notification.style.transform = 'translateY(-20px)';
    
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 300);
}

// Log a message both to console and as a notification for critical issues
function logCriticalError(message, error) {
    console.error(message, error);
    showNotification(message, 'error', 10000);
    
    // Also add to browser console
    if (error && error.stack) {
        console.error(error.stack);
    }
}

// Add to the RF_SCYTHE namespace
window.addEventListener('load', function() {
    setTimeout(function() {
        if (window.RF_SCYTHE) {
            window.RF_SCYTHE.showNotification = showNotification;
            window.RF_SCYTHE.logCriticalError = logCriticalError;
            console.log('Notification system added to RF_SCYTHE');
        } else {
            console.warn('RF_SCYTHE namespace not found, could not add notification system');
        }
    }, 500);
});
