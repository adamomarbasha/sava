import React from 'react';

const InstagramThumbnail = ({ post }) => {
  const getThumbnailUrl = (post) => {
    if (post.thumbnail_url) {
      return post.thumbnail_url;
    }
    
    if (post.images && post.images.length > 0) {
      return post.images[0].url;
    }
    
    return '/placeholder-instagram.jpg';
  };

  return (
    <div className="instagram-thumbnail">
      <img 
        src={getThumbnailUrl(post)} 
        alt={post.caption || 'Instagram post'}
        className="w-full h-48 object-cover rounded-lg"
      />
      <div className="p-2">
        <p className="text-sm text-gray-600 truncate">
          {post.caption || 'No caption'}
        </p>
        <div className="flex justify-between items-center mt-2">
          <span className="text-xs text-gray-500">
            {new Date(post.timestamp).toLocaleDateString()}
          </span>
          <span className="text-xs text-blue-500">
            @{post.username}
          </span>
        </div>
      </div>
    </div>
  );
};

export default InstagramThumbnail;
