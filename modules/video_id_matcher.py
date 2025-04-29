#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import os
import json
import logging
import chardet

class VideoIDMatcher:
    """Video ID matcher for STRM files"""
    
    def __init__(self, dictionary_path=None):
        """Initialize the video ID matcher
        
        Args:
            dictionary_path: Path to the dictionary file for filtering
        """
        self.dictionary = []  # For storing strings to be excluded
        self.video_extensions = ['.mp4', '.avi', '.mkv', '.wmv', '.rmvb', '.mov', '.m4v', '.flv', '.vob', '.ts', '.m2ts']
        self.image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp']
        
        # If dictionary path is provided, load it
        if dictionary_path and os.path.exists(dictionary_path):
            self.load_dictionary(dictionary_path)
    
    def load_dictionary(self, dict_file):
        """Load exclusion dictionary from file
        
        Args:
            dict_file: Path to the dictionary file
            
        Returns:
            int: Number of dictionary entries loaded
        """
        try:
            # Try to auto-detect encoding
            encoding = self.detect_file_encoding(dict_file)
            with open(dict_file, 'r', encoding=encoding) as f:
                self.dictionary = [line.strip() for line in f if line.strip()]
            return len(self.dictionary)
        except Exception as e:
            logging.error(f"Error loading dictionary file: {e}")
            return 0
    
    def load_dictionary_from_json(self, json_dictionary):
        """Load dictionary from JSON string or object
        
        Args:
            json_dictionary: JSON string or list of dictionary entries
            
        Returns:
            int: Number of dictionary entries loaded
        """
        try:
            if isinstance(json_dictionary, str):
                # Parse JSON string
                dictionary_data = json.loads(json_dictionary)
            else:
                # Assume it's already parsed
                dictionary_data = json_dictionary
            
            if isinstance(dictionary_data, list):
                self.dictionary = [item.strip() for item in dictionary_data if item.strip()]
            else:
                logging.error("Invalid dictionary format: expected a list")
                return 0
            
            return len(self.dictionary)
        except Exception as e:
            logging.error(f"Error loading dictionary from JSON: {e}")
            return 0
    
    def detect_file_encoding(self, file_path):
        """Detect file encoding
        
        Args:
            file_path: Path to the file
            
        Returns:
            str: Detected encoding or 'utf-8' as fallback
        """
        try:
            with open(file_path, 'rb') as f:
                raw_data = f.read(4096)  # Read part of file for detection
                result = chardet.detect(raw_data)
                return result['encoding'] or 'utf-8'
        except Exception:
            return 'utf-8'  # Default to UTF-8
    
    def clean_filename(self, filename):
        """Clean filename by removing unwanted parts using dictionary
        
        Args:
            filename: The filename to clean
            
        Returns:
            str: Cleaned filename
        """
        cleaned = filename
        for item in self.dictionary:
            cleaned = cleaned.replace(item, '')
        return cleaned
    
    def extract_video_id(self, filename):
        """Extract standardized video ID from cleaned filename
        
        Args:
            filename: The filename to process
            
        Returns:
            str: Extracted video ID or empty string if not found
        """
        # Get just the filename part
        base_name = os.path.basename(filename)
        
        # Clean the filename
        cleaned_name = self.clean_filename(base_name)
        
        # Pre-process: Remove suffixes like -1, -2, -3 (hyphen followed by single digit)
        # Be careful not to affect normal IDs like ABC-123
        # Handle cases with extensions, e.g., "dphn101-1.avi" -> "dphn101.avi"
        cleaned_name = re.sub(r'(.*?)(-\d)(\.(mp4|avi|mkv|wmv|rmvb|mov|m4v|flv|vob|ts|m2ts))$', r'\1\3', cleaned_name, flags=re.IGNORECASE)
        # Handle cases without extensions, e.g., "dphn101-1" -> "dphn101"
        cleaned_name = re.sub(r'(.*?)(-\d)$', r'\1', cleaned_name)
        
        # Common video file extension pattern
        video_extensions = r'\.(mp4|avi|mkv|wmv|rmvb|mov|m4v|flv|vob|ts|m2ts|strm)$'
        
        # Remove extension
        name_without_ext = re.sub(video_extensions, '', cleaned_name, flags=re.IGNORECASE)
        
        # Try to match common video ID patterns
        patterns = [
            # Match IDs like ABC-123, ABCD-123
            r'([A-Z0-9]+-\d+)',
            # Match IDs in brackets like [ABC-123], (ABC-123)
            r'[\[\(]([A-Z0-9]+-\d+)[\]\)]',
            # Match date format IDs like 041815_064
            r'(\d{6}_\d{3})',
            # Match date format IDs like 041015-850
            r'(\d{6}-\d{3})',
            # Match ABC-123 at start of string
            r'^([A-Z0-9]+-\d+)',
            # Match ABC-123 at end of string
            r'([A-Z0-9]+-\d+)$',
            # Match IDs without hyphens like ABC123
            r'([A-Z]+\d+)',
            # Match specific format like dphn101
            r'((?:dphn|dph)\d+)',
        ]
        
        # Try all patterns
        for pattern in patterns:
            match = re.search(pattern, name_without_ext, re.IGNORECASE)
            if match:
                # Extract and standardize the ID, convert to uppercase
                video_id = match.group(1).upper()
                
                # Handle cases without hyphen, try to add it
                if '-' not in video_id:
                    # Special case for dphn101 format to dphn-101
                    dphn_match = re.search(r'(DPHN|DPH)(\d+)', video_id, re.IGNORECASE)
                    if dphn_match:
                        video_id = f"{dphn_match.group(1)}-{dphn_match.group(2)}"
                    else:
                        # Try to add hyphen between letters and numbers
                        alpha_num_match = re.search(r'([A-Z]+)(\d+)', video_id, re.IGNORECASE)
                        if alpha_num_match:
                            video_id = f"{alpha_num_match.group(1)}-{alpha_num_match.group(2)}"
                
                # Handle IDs like ABC-00123, remove leading "00"
                five_digit_match = re.search(r'(.*?)-00(\d{3})$', video_id)
                if five_digit_match:
                    # Reconstruct ID, removing leading "00"
                    prefix = five_digit_match.group(1)
                    number = five_digit_match.group(2)
                    video_id = f"{prefix}-{number}"
                
                # Special case for N-xxxx format, remove hyphen
                n_match = re.search(r'^N-(\d+)$', video_id)
                if n_match:
                    # For IDs like N-1234, remove hyphen to N1234
                    video_id = f"N{n_match.group(1)}"

                # Special case for K-xxxx format, remove hyphen
                k_match = re.search(r'^K-(\d+)$', video_id)
                if k_match:
                    # For IDs like K-1234, remove hyphen to K1234
                    video_id = f"K{k_match.group(1)}"

                return video_id
        
        # If no pattern matched, return empty string
        return ""
    
    def process_strm_files(self, strm_files):
        """Process STRM files to extract video IDs
        
        Args:
            strm_files: List of STRM file objects
            
        Returns:
            list: List of dictionaries with STRM file info and video IDs
        """
        results = []
        for strm_file in strm_files:
            # Extract filename from filepath
            filename = os.path.basename(strm_file.get('filepath', ''))
            title = strm_file.get('title', '')
            
            # Extract video ID from both filename and title
            file_id = self.extract_video_id(filename)
            title_id = self.extract_video_id(title)
            
            # Use whichever ID was successfully extracted, prioritizing filename
            video_id = file_id or title_id
            
            if video_id:
                results.append({
                    'id': strm_file.get('id'),
                    'filepath': strm_file.get('filepath', ''),
                    'title': title,
                    'filename': filename,
                    'video_id': video_id,
                    'original_id': strm_file.get('video_id', '')
                })
        
        return results
    
    def update_strm_title(self, strm_file, video_id):
        """Update STRM file title with video ID
        
        Args:
            strm_file: STRM file object
            video_id: Extracted video ID
            
        Returns:
            str: Updated title
        """
        title = strm_file.get('title', '')
        
        # If title already contains the video ID, leave it
        if video_id in title:
            return title
        
        # Just return the video ID as the title
        return video_id 